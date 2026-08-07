#!/usr/bin/env python3
"""Minimal executable workflow runner with bounded retries and trace storage."""

from __future__ import annotations

import argparse
import json
import random
import shlex
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_state import RunLayout, find_completed_run, record_failure, task_fingerprint


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.observability import safe_telemetry_runtime
from ai_harness.recovery import RecoveryCoordinator, classify_failure, load_recovery_policy
from ai_harness.recovery.checkpoints import RoleCheckpoint, write_checkpoint
from ai_harness.recovery.models import FailureRecord, persist_failure

WORKFLOWS = ROOT / ".agent-workflows.yaml"
RUNS_DIR = ROOT / ".agent-runs"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_BUDGETS = {
    "max_roles": 40,
    "max_repair_iterations": 12,
    "max_duration_seconds": 7200,
    "max_tokens": 300000,
}
EXIT_COMPLETED = 0
EXIT_AWAITING_APPROVAL = 10
EXIT_RETRYABLE_FAILURE = 20
EXIT_REPAIRABLE_FAILURE = 21
EXIT_RESUMABLE_FAILURE = 22
EXIT_DEAD_LETTER = 30
EXIT_UNRECOVERABLE_FAILURE = 40
EXIT_INVALID_HARNESS_STATE = 50
RECOVERY_EXIT_CODES = {
    "retry": EXIT_RETRYABLE_FAILURE,
    "repair": EXIT_REPAIRABLE_FAILURE,
    "resume": EXIT_RESUMABLE_FAILURE,
    "approval": EXIT_AWAITING_APPROVAL,
    "dead_letter": EXIT_DEAD_LETTER,
    "fail": EXIT_UNRECOVERABLE_FAILURE,
}
AUTHORITATIVE_PAUSE_STATUSES = {
    "awaiting_approval",
    "blocked",
    "retry_wait",
    "repairing",
    "resuming",
    "dead_letter",
    "failed",
}


@dataclass
class StepResult:
    name: str
    command: str
    attempt: int
    returncode: int
    stdout: str
    stderr: str

    def as_json(self) -> dict[str, Any]:
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "step": self.name,
            "command": self.command,
            "attempt": self.attempt,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def run_command(command: str, cwd: Path, timeout_seconds: int) -> tuple[int, str, str]:
    argv = shlex.split(command)
    if argv and argv[0] == "python3":
        argv[0] = sys.executable
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"command timed out after {timeout_seconds}s"
    except PermissionError as exc:
        return 126, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def read_workflows(path: Path | None = None) -> dict[str, Any]:
    workflow_path = path or WORKFLOWS
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(".agent-workflows.yaml must contain an object")
    return data


def make_run_id(workflow_name: str) -> str:
    suffix = "".join(random.SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(6))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{timestamp}-{workflow_name}-{suffix}"


def make_run_dir(workflow_name: str, run_id: str = "") -> Path:
    run_id = run_id or make_run_id(workflow_name)
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def append_trace(layout: RunLayout, event: dict[str, Any]) -> None:
    with (layout.raw_events / "workflow-runner.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def workflow_budgets(workflow: dict[str, Any]) -> dict[str, int]:
    budgets = dict(DEFAULT_BUDGETS)
    configured = workflow.get("budgets")
    if isinstance(configured, dict):
        for key in budgets:
            if isinstance(configured.get(key), (int, float)):
                budgets[key] = int(configured[key])
    return budgets


def workflow_steps(workflow: dict[str, Any]) -> list[dict[str, str]]:
    steps = workflow.get("steps", [])
    if isinstance(steps, list) and steps:
        return [step for step in steps if isinstance(step, dict)]
    executor = workflow.get("executor")
    if isinstance(executor, str) and executor:
        return [{"name": "executor", "command": executor}]
    return []


def quote_placeholder(value: str | Path) -> str:
    return shlex.quote(str(value))


def read_workflow_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_workflow_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def workflow_pause_scheduled(path: Path) -> bool:
    """Return whether the authoritative child workflow already selected its next action."""

    return str(read_workflow_state(path).get("execution_status", "")) in AUTHORITATIVE_PAUSE_STATUSES


def recovery_attempt(state: dict[str, Any], kind: str) -> int:
    recovery = state.get("recovery", {})
    by_kind = recovery.get("attempts_by_kind", {}) if isinstance(recovery, dict) else {}
    return int(by_kind.get(kind, 0) or 0) + 1 if isinstance(by_kind, dict) else 1


def record_recovery_failure(
    *,
    layout: RunLayout,
    state: dict[str, Any],
    process_returncode: int,
    message: str,
    step: str,
) -> tuple[int, dict[str, Any]]:
    policy = load_recovery_policy()
    role = str(state.get("current_role", step))
    preliminary = classify_failure(
        RuntimeError(message),
        process_returncode,
        state,
        run_id=layout.run_id,
        task_id=str(state.get("task_id", "task")),
        role=role,
        stage="workflow_step",
    )
    configured = policy.for_kind(preliminary.kind)
    attempt = recovery_attempt(state, preliminary.kind)
    checkpoint = f"before_{role.replace('-', '_')}"
    failure = classify_failure(
        RuntimeError(message),
        process_returncode,
        state,
        run_id=layout.run_id,
        task_id=str(state.get("task_id", "task")),
        role=role,
        stage="workflow_step",
        checkpoint=checkpoint,
        attempt=attempt,
        max_attempts=configured.max_attempts,
    )
    decision = RecoveryCoordinator().decide(failure, state, policy)
    persist_failure(layout.root, failure)
    recovery = state.get("recovery", {})
    if not isinstance(recovery, dict):
        recovery = {}
    by_kind = recovery.get("attempts_by_kind", {})
    if not isinstance(by_kind, dict):
        by_kind = {}
    by_kind[failure.kind] = attempt
    recovery.update(
        {
            "started_at": float(recovery.get("started_at", time.time()) or time.time()),
            "attempts": int(recovery.get("attempts", 0) or 0) + 1,
            "consecutive_failures": int(recovery.get("consecutive_failures", 0) or 0) + 1,
            "resume_attempts": int(recovery.get("resume_attempts", 0) or 0) + (1 if decision.action == "resume" else 0),
            "attempts_by_kind": by_kind,
        }
    )
    recovery["elapsed_seconds"] = int(max(0, time.time() - float(recovery["started_at"])))
    state.update(
        {
            "execution_status": decision.next_status,
            "current_role": role,
            "resume_role": role,
            "resume_from": failure.checkpoint,
            "failure_id": failure.failure_id,
            "failure_kind": failure.kind,
            "recovery_action": decision.action,
            "recovery_reason": decision.reason,
            "retry_after_seconds": decision.delay_seconds,
            "recovery": recovery,
        }
    )
    write_workflow_state(layout.workflow, state)
    return RECOVERY_EXIT_CODES[decision.action], failure.as_json()


def run_workflow(
    workflow_name: str,
    dry_run: bool = False,
    root: Path = ROOT,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    run_id: str = "",
    task_id: str = "task",
    goal: str = "",
    project: str = "",
    repository: Path | None = None,
    branch: str = "",
    base_branch: str = "main",
    current_branch: bool = False,
    mode: str = "auto",
    adapter_command: str = "",
    resume: bool = False,
    runtime_provider: str = "",
    runtime_command: str = "",
) -> int:
    if mode not in {"auto", "fast", "full"}:
        raise ValueError("mode must be auto, fast, or full")
    workflows = read_workflows()
    workflow = workflows.get("workflows", {}).get(workflow_name)
    if not isinstance(workflow, dict):
        print(f"unknown workflow: {workflow_name}")
        return EXIT_INVALID_HARNESS_STATE
    project_value = project or "agent_workspace"
    goal_value = goal or task_id
    branch_value = branch or f"issue/{task_id}"
    repository_value = (repository or root).resolve()
    fingerprint = task_fingerprint(
        task_id=task_id,
        goal=goal_value,
        repository=repository_value,
        branch=branch_value,
        base_branch=base_branch,
        workspace_mode="current_branch" if current_branch else "isolated",
        workflow_mode=mode,
    )
    existing = find_completed_run(RUNS_DIR, fingerprint, exclude_run_id="") if not resume else None
    if existing is not None:
        print(str(RUNS_DIR / str(existing.get("run_id", ""))))
        return 0
    run_id = run_id or make_run_id(workflow_name)
    run_dir = make_run_dir(workflow_name, run_id)
    layout = RunLayout.create(RUNS_DIR, run_dir.name)
    max_iterations = int(workflow.get("max_iterations", 1))
    workflow_timeout_seconds = int(workflow.get("timeout_seconds", timeout_seconds))
    retry = workflow.get("retry", {})
    max_retries = int(retry.get("max_retries", 0)) if isinstance(retry, dict) else 0
    backoff_seconds = float(retry.get("backoff_seconds", 0)) if isinstance(retry, dict) else 0.0
    steps = workflow_steps(workflow)
    effective_runtime_provider = runtime_provider or str(workflow.get("runtime_provider", ""))
    effective_runtime_command = runtime_command or adapter_command or str(
        workflow.get("runtime_command", workflow.get("adapter_command", ""))
    )
    artifacts_dir = layout.artifacts
    budgets = workflow_budgets(workflow)
    started = time.monotonic()
    telemetry = safe_telemetry_runtime(
        run_dir=run_dir,
        service_name="ai-harness-workflow",
        service_instance_id=run_id,
    )
    root_span = telemetry.tracer.start_span(
        "ai_harness.workflow",
        context=telemetry.extracted_context(),
        attributes={
            "workflow.name": workflow_name,
            "run.id": run_id,
            "task.id": task_id,
            "workflow.max_iterations": max_iterations,
            "workflow.max_retries": max_retries,
            "workflow.resume": resume,
            "workflow.dry_run": dry_run,
        },
    )
    root_context = trace.set_span_in_context(root_span)
    total_retries = 0
    iterations_completed = 0

    def finish(result_code: int, outcome: str, *, error_type: str = "") -> int:
        duration = max(0, time.monotonic() - started)
        root_span.set_attribute("workflow.outcome", outcome)
        root_span.set_attribute("workflow.iterations", iterations_completed)
        root_span.set_attribute("workflow.retries", total_retries)
        root_span.set_attribute("workflow.duration_seconds", duration)
        if error_type:
            root_span.set_attribute("error.type", error_type)
            root_span.set_status(Status(StatusCode.ERROR))
            telemetry.failure_counter.add(1, {"operation": "workflow", "error.type": error_type})
        else:
            root_span.set_status(Status(StatusCode.OK))
        telemetry.loop_counter.add(iterations_completed, {"workflow": workflow_name})
        telemetry.retry_counter.add(total_retries, {"workflow": workflow_name})
        telemetry.duration_histogram.record(duration, {"operation": "workflow"})
        telemetry.task_counter.add(1, {"outcome": outcome})
        root_span.end()
        telemetry.shutdown()
        return result_code

    workflow_state_path = layout.workflow
    if not workflow_state_path.exists():
        workflow_state_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "workflow": workflow_name,
                    "task_id": task_id,
                    "goal": goal_value,
                    "execution_status": "running",
                    "mode": mode,
                    "roles": [],
                    "loops": {
                        "quality_repair": {"iterations": 0},
                        "review_repair": {"iterations": 0},
                        "ci_repair": {"iterations": 0},
                        "frontend_verification_repair": {"iterations": 0},
                    },
                    "budgets": budgets,
                    "role_count": 0,
                    "tokens_used": 0,
                    "input_fingerprint": fingerprint,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    elif resume:
        resume_state = read_workflow_state(workflow_state_path)
        if resume_state.get("execution_status") in {"retry_wait", "repairing", "resuming"}:
            resume_state["execution_status"] = "resuming"
            write_workflow_state(workflow_state_path, resume_state)
    append_trace(
        layout,
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": "workflow_started",
            "workflow": workflow_name,
            "dry_run": dry_run,
            "max_iterations": max_iterations,
            "budgets": budgets,
        },
    )
    for iteration in range(1, max_iterations + 1):
        iterations_completed = iteration
        append_trace(layout, {"time": datetime.now(timezone.utc).isoformat(), "event": "iteration_started", "iteration": iteration})
        root_span.add_event("workflow.iteration", {"iteration": iteration})
        for step in steps:
            if time.monotonic() - started > budgets["max_duration_seconds"]:
                state_data = read_workflow_state(workflow_state_path)
                failure = FailureRecord.create(
                    run_id=run_id,
                    task_id=task_id,
                    role=str(state_data.get("current_role", "workflow")),
                    stage="budget",
                    kind="human_input_required",
                    error_type="WorkflowDurationExceeded",
                    message="max_duration_seconds exceeded",
                    retryable=False,
                    repairable=False,
                    checkpoint=f"before_{str(state_data.get('current_role', 'workflow')).replace('-', '_')}",
                )
                persist_failure(layout.root, failure)
                state_data.update(
                    {
                        "execution_status": "awaiting_approval",
                        "failure_id": failure.failure_id,
                        "failure_kind": failure.kind,
                        "recovery_action": "approval",
                        "recovery_reason": "workflow duration budget exceeded",
                    }
                )
                write_workflow_state(workflow_state_path, state_data)
                append_trace(
                    layout,
                    {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "event": "workflow_awaiting_approval",
                        "reason": "max_duration_seconds exceeded",
                    },
                )
                print(str(run_dir))
                return finish(EXIT_AWAITING_APPROVAL, "awaiting_approval")
            name = str(step.get("name", "step"))
            current_state = read_workflow_state(workflow_state_path)
            checkpoint_role = name
            checkpoint_attempt = recovery_attempt(current_state, str(current_state.get("failure_kind", "runtime_failure")))
            write_checkpoint(
                run_dir,
                RoleCheckpoint(
                    run_id=run_id,
                    role=checkpoint_role,
                    state="role_pending",
                    attempt=max(1, checkpoint_attempt),
                    worktree=str(current_state.get("worktree", repository_value)),
                    input_fingerprint=fingerprint,
                ),
            )
            command = str(step.get("command", ""))
            command = (
                command.replace("{run_id}", quote_placeholder(run_id))
                .replace("{run_dir}", quote_placeholder(run_dir))
                .replace("{artifacts_dir}", quote_placeholder(artifacts_dir))
                .replace("{task_id}", quote_placeholder(task_id))
                .replace("{goal}", quote_placeholder(goal_value))
                .replace("{project}", quote_placeholder(project_value))
                .replace("{repository}", quote_placeholder(repository_value))
                .replace("{branch}", quote_placeholder(branch_value))
                .replace("{base_branch}", quote_placeholder(base_branch))
                .replace("{runtime_provider}", quote_placeholder(effective_runtime_provider))
                .replace("{runtime_command}", quote_placeholder(effective_runtime_command))
                .replace("{adapter_command}", quote_placeholder(effective_runtime_command))
            )
            if (
                effective_runtime_command
                and command.startswith("python3 scripts/agent_role_runner.py")
                and "--runtime-command" not in command
                and "--adapter-command" not in command
            ):
                legacy_adapter = "adapter_command" in workflow and "runtime_provider" not in workflow
                flag = "--adapter-command" if legacy_adapter else "--runtime-command"
                command = f"{command} {flag} {quote_placeholder(effective_runtime_command)}"
            if (
                effective_runtime_provider
                and command.startswith("python3 scripts/agent_role_runner.py")
                and "--runtime-provider" not in command
            ):
                command = f"{command} --runtime-provider {quote_placeholder(effective_runtime_provider)}"
            if (
                dry_run
                and command.startswith(("python3 scripts/publish_pr.py", "python3 scripts/agent_role_runner.py"))
                and "--dry-run" not in command
            ):
                command = command + " --dry-run"
            if resume and command.startswith("python3 scripts/agent_role_runner.py") and "--resume" not in command:
                command = command + " --resume"
            if command.startswith("python3 scripts/agent_role_runner.py") and "--mode" not in command:
                command = f"{command} --mode {quote_placeholder(mode)}"
            if (
                current_branch
                and command.startswith("python3 scripts/agent_role_runner.py")
                and "--current-branch" not in command
            ):
                command = command + " --current-branch"
            for attempt in range(1, max_retries + 2):
                if attempt > 1:
                    total_retries += 1
                    root_span.add_event("workflow.retry", {"step.name": name, "attempt": attempt})
                with telemetry.span(
                    "ai_harness.workflow.step",
                    {
                        "step.name": name,
                        "step.attempt": attempt,
                        "workflow.iteration": iteration,
                    },
                    context=root_context,
                ) as step_span:
                    write_checkpoint(
                        run_dir,
                        RoleCheckpoint(
                            run_id=run_id,
                            role=checkpoint_role,
                            state="role_running",
                            attempt=max(1, checkpoint_attempt),
                            worktree=str(current_state.get("worktree", repository_value)),
                            input_fingerprint=fingerprint,
                        ),
                    )
                    step_started = time.monotonic()
                    returncode, stdout, stderr = run_command(command, root, workflow_timeout_seconds)
                    step_span.set_attribute("step.return_code", returncode)
                    step_span.set_attribute("step.duration_seconds", max(0, time.monotonic() - step_started))
                    if returncode != 0:
                        step_span.set_status(Status(StatusCode.ERROR))
                result = StepResult(name, command, attempt, returncode, stdout, stderr)
                result_event = result.as_json()
                result_event["iteration"] = iteration
                append_trace(layout, result_event)
                write_checkpoint(
                    run_dir,
                    RoleCheckpoint(
                        run_id=run_id,
                        role=checkpoint_role,
                        state="role_output_received",
                        attempt=max(1, checkpoint_attempt),
                        worktree=str(current_state.get("worktree", repository_value)),
                        input_fingerprint=fingerprint,
                    ),
                )
                if returncode == 0:
                    break
                if workflow_pause_scheduled(workflow_state_path):
                    break
                if attempt <= max_retries:
                    time.sleep(backoff_seconds)
            if returncode != 0:
                state_data = read_workflow_state(workflow_state_path)
                status = str(state_data.get("execution_status", ""))
                if status == "blocked":
                    append_trace(layout, {"time": datetime.now(timezone.utc).isoformat(), "event": "workflow_blocked", "step": name})
                    print(str(run_dir))
                    return finish(EXIT_AWAITING_APPROVAL, "blocked", error_type="WorkflowBlocked")
                if status in {"retry_wait", "repairing", "resuming", "dead_letter", "failed", "awaiting_approval"}:
                    action = str(state_data.get("recovery_action", ""))
                    if status == "awaiting_approval":
                        action = "approval"
                    elif status == "dead_letter":
                        action = "dead_letter"
                    elif status == "failed":
                        action = "fail"
                    elif not action:
                        action = {"retry_wait": "retry", "repairing": "repair", "resuming": "resume"}[status]
                    append_trace(layout, {"time": datetime.now(timezone.utc).isoformat(), "event": "workflow_recovery_scheduled", "step": name, "action": action})
                    with telemetry.span(
                        f"ai_harness.recovery.{action if action != 'fail' else 'dead_letter'}",
                        {"failure.kind": str(state_data.get("failure_kind", "")), "recovery.action": action},
                        context=root_context,
                    ):
                        pass
                    telemetry.recovery_attempts_total.add(1, {"action": action})
                    if action == "retry":
                        telemetry.task_retries_total.add(1)
                    elif action == "repair":
                        telemetry.output_repairs_total.add(1)
                    elif action == "dead_letter":
                        telemetry.recovery_exhausted_total.add(1)
                    print(str(run_dir))
                    return finish(RECOVERY_EXIT_CODES[action], status, error_type=str(state_data.get("failure_kind", "RecoveryFailure")))
                append_trace(
                    layout,
                    {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "event": "workflow_failed",
                        "step": name,
                        "returncode": returncode,
                    },
                )
                with telemetry.span("ai_harness.recovery.classify", {"step.name": name}, context=root_context):
                    recovery_code, failure = record_recovery_failure(
                        layout=layout,
                        state=state_data,
                        process_returncode=returncode,
                        message=stderr or stdout or f"exit {returncode}",
                        step=name,
                    )
                scheduled = read_workflow_state(workflow_state_path)
                action = str(scheduled.get("recovery_action", "retry"))
                span_action = action if action in {"retry", "repair", "resume", "dead_letter"} else "dead_letter"
                with telemetry.span(
                    f"ai_harness.recovery.{span_action}",
                    {"failure.kind": str(failure["kind"]), "recovery.action": action},
                    context=root_context,
                ):
                    pass
                telemetry.recovery_attempts_total.add(1, {"action": action})
                if action == "retry":
                    telemetry.task_retries_total.add(1)
                elif action == "dead_letter":
                    telemetry.recovery_exhausted_total.add(1)
                print(str(run_dir))
                return finish(recovery_code, str(read_workflow_state(workflow_state_path).get("execution_status", "retry_wait")), error_type=str(failure["error_type"]))
            state_data = read_workflow_state(workflow_state_path)
            if isinstance(state_data, dict) and state_data.get("execution_status") == "awaiting_approval":
                append_trace(
                    layout,
                    {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "event": "workflow_awaiting_approval",
                        "reason": "authoritative role router requested approval",
                    },
                )
                print(str(run_dir))
                return finish(EXIT_AWAITING_APPROVAL, "awaiting_approval")
            write_checkpoint(
                run_dir,
                RoleCheckpoint(
                    run_id=run_id,
                    role=checkpoint_role,
                    state="role_completed",
                    attempt=max(1, checkpoint_attempt),
                    worktree=str(state_data.get("worktree", repository_value)),
                    input_fingerprint=fingerprint,
                    artifacts=[str(item) for item in state_data.get("artifacts", []) if isinstance(item, str)],
                ),
            )
    append_trace(layout, {"time": datetime.now(timezone.utc).isoformat(), "event": "workflow_completed"})
    completed_state = read_workflow_state(workflow_state_path)
    recovery_state = completed_state.get("recovery", {})
    if isinstance(recovery_state, dict) and int(recovery_state.get("attempts", 0) or 0) > 0:
        telemetry.recovery_success_total.add(1)
        if int(completed_state.get("resume_count", 0) or 0) > 0:
            telemetry.resume_success_total.add(1)
    print(str(run_dir))
    return finish(0, "completed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", nargs="?", default="publish_pr")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--task-id", default="task")
    parser.add_argument("--goal", default="")
    parser.add_argument("--project", default="agent_workspace")
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--branch", default="")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--current-branch", action="store_true")
    parser.add_argument("--mode", choices=("auto", "fast", "full"), default="auto")
    parser.add_argument("--adapter-command", default="")
    parser.add_argument("--runtime-provider", default="")
    parser.add_argument("--runtime-command", default="")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return run_workflow(
            args.workflow,
            dry_run=args.dry_run,
            timeout_seconds=args.timeout_seconds,
            run_id=args.run_id,
            task_id=args.task_id,
            goal=args.goal,
            project=args.project,
            repository=args.repo,
            branch=args.branch,
            base_branch=args.base_branch,
            current_branch=args.current_branch,
            mode=args.mode,
            adapter_command=args.adapter_command,
            resume=args.resume,
            runtime_provider=args.runtime_provider,
            runtime_command=args.runtime_command,
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"invalid harness state: {exc}", file=sys.stderr)
        return EXIT_INVALID_HARNESS_STATE


if __name__ == "__main__":
    raise SystemExit(main())
