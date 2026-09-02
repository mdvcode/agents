#!/usr/bin/env python3
"""Run the agent-role workflow through a provider-neutral runtime boundary."""

from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opentelemetry.trace import Status, StatusCode


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
HARNESS_ROOT = SCRIPT_DIR.parent
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

from approval_lifecycle import (
    ApprovalError,
    MODEL_ESCALATION_REQUIREMENT,
    MODEL_ESCALATION_SUMMARY,
    approval_lock,
    bounded_model_escalation_checkpoint,
    model_escalation_fingerprint,
    model_escalation_terminal_state,
    read_json as read_approval_workflow,
    request_approval,
    write_json_atomic as write_approval_workflow,
)
from ai_harness.model_policy import select_execution_profile
from ai_harness.economics import BudgetAction, BudgetController, BudgetUsage
from ai_harness.execution_accounting import (
    accounted_role_count,
    accounted_tokens_used,
    incremental_tokens,
    role_entry_invoked_model,
)
from ai_harness.planning import RolePolicy, TaskAnalyzer, WorkflowCompiler
from ai_harness.planning.workflow_compiler import COMPILER_VERSION
from context_compiler import create_context_manifest, role_capability, role_contract
from repository_registry import RepositoryRecord, find_by_remote, load_local_project_record
from runtime_contracts import contract_section, load_json, validate_contract
from runtimes import RuntimeConfigurationError, create_runtime
from run_state import (
    RunLayout,
    file_contents_snapshot,
    file_snapshot,
    find_completed_run,
    ownership_errors,
    record_failure,
    restore_foreign_artifacts,
    task_fingerprint,
    write_metrics,
)
from tool_preflight import role_tool_preflight
from task_graph import (
    TaskGraphError,
    join_parent_children,
    spawn_children,
)
from task_queue import DEFAULT_DB, TaskQueue
from validate_artifacts import validate_required as validate_artifact_required
from workflow_router import changed_areas as workflow_changed_areas
from workflow_router import changed_files as workflow_changed_files
from workflow_router import decide_next_role, load_yaml, reviewer_requires_llm
from worktree_manager import create_worktree, inspect_current_checkout, slug, use_current_checkout
from ai_harness.recovery import RecoveryCoordinator, classify_failure, load_recovery_policy
from ai_harness.recovery.checkpoints import (
    CheckpointError,
    RoleCheckpoint,
    read_checkpoint,
    resume_operation as checkpoint_resume_operation,
    write_checkpoint,
)
from ai_harness.recovery.models import FailureRecord, persist_failure
from ai_harness.observability import safe_telemetry_runtime


ROOT = HARNESS_ROOT
PROMPTS = ROOT / ".agents" / "prompts"
RUNS = ROOT / ".agent-runs"
SCHEMAS = ROOT / "schemas"
ROLE_CHAIN = [
    "issue-intake",
    "context-compiler",
    "planner",
    "risk-classifier",
    "implementation-agent",
    "test-generator",
    "quality-runner",
    "security-agent",
    "frontend-qa-agent",
    "architecture-consistency-agent",
    "semantic-conflict-agent",
    "reviewer",
    "ci-repair-agent",
    "orchestrator",
    "eval-runner",
    "report-agent",
    "publication-prepare",
    "publication",
]
INTERNAL_ROLES = {"issue-intake", "context-compiler", "publication-prepare"}
ADAPTER_ROLES = set(ROLE_CHAIN) - INTERNAL_ROLES - {"publication"}


def serialized_run_execution(function: Any) -> Any:
    """Hold one OS-backed execution lease for every write in a named run."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        run_id = str(kwargs.get("run_id", ""))
        if not run_id and len(args) > 1:
            run_id = str(args[1])
        if not run_id:
            return function(*args, **kwargs)
        if Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError(f"unsafe run id: {run_id!r}")
        run_dir = (RUNS / run_id).resolve()
        if run_dir.parent != RUNS.resolve():
            raise ValueError(f"run directory escapes .agent-runs: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = run_dir / "runner.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                return function(*args, **kwargs)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    return wrapped


def task_graph_metadata() -> dict[str, Any]:
    raw = os.environ.get("AGENT_TASK_GRAPH_METADATA", "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def child_result_context(state: dict[str, Any]) -> str:
    children = state.get("children", [])
    if not isinstance(children, list) or not children:
        return ""
    lines = ["# BACKGROUND CHILD RESULTS"]
    for child in children[:3]:
        if not isinstance(child, dict):
            continue
        lines.append(
            "- "
            + "; ".join(
                (
                    f"run={child.get('run_id', '')}",
                    f"status={child.get('status', '')}",
                    f"join={child.get('join_status', '')}",
                    f"detail={str(child.get('join_detail', ''))[:500]}",
                )
            )
        )
    problems = state.get("child_join_problems", [])
    if isinstance(problems, list):
        lines.extend(f"- action required: {str(item)[:1000]}" for item in problems[:3])
    return "\n".join(lines)
PROMPT_FILES = {
    "context-compiler": "context-compiler.md",
    "planner": "planner.md",
    "risk-classifier": "risk-classifier.md",
    "implementation-agent": "implementation-agent.md",
    "test-generator": "test-generator.md",
    "quality-runner": "quality-runner.md",
    "security-agent": "security-agent.md",
    "frontend-qa-agent": "frontend-qa-agent.md",
    "architecture-consistency-agent": "architecture-consistency-agent.md",
    "semantic-conflict-agent": "semantic-conflict-agent.md",
    "reviewer": "reviewer.md",
    "ci-repair-agent": "ci-repair-agent.md",
    "orchestrator": "orchestrator.md",
    "eval-runner": "eval-runner.md",
    "report-agent": "report-agent.md",
}
DEFAULT_ALLOWED_TOOLS = ["filesystem_read", "repository_search"]
KNOWN_PROJECT_PROFILES = {"agent_workspace", "django", "nextjs_web"}
EXECUTION_MODES = {"auto", "adaptive", "fast", "full", "goal"}
FULL_HINTS = (
    "auth", "permission", "migration", "billing", "payment", "secret", "production", "deploy",
    "database schema", "public api", "dependency", "package upgrade", "architecture", "refactor",
    "авторизац", "разрешен", "миграц", "оплат", "секрет", "продакш", "деплой", "схем",
    "зависимост", "архитектур", "рефактор",
)
ADAPTIVE_ACCEPTANCE = ROOT / "evals" / "adaptive_execution_acceptance.json"
QUESTION_STOP_WORDS = {
    "a",
    "an",
    "be",
    "choose",
    "do",
    "for",
    "is",
    "need",
    "please",
    "required",
    "select",
    "should",
    "the",
    "to",
    "use",
    "used",
    "what",
    "which",
    "would",
    "выбрать",
    "для",
    "использовать",
    "какой",
    "нужен",
    "нужно",
    "следует",
}
QUESTION_TOKEN_ALIASES = {
    "database": "database",
    "db": "database",
    "choice": "option",
    "destination": "target",
    "directory": "path",
    "environment": "env",
    "export": "output",
    "folder": "path",
    "location": "path",
    "preference": "option",
    "result": "output",
    "variant": "option",
    "вариант": "option",
    "бд": "database",
    "выбор": "option",
    "каталог": "path",
    "окружение": "env",
    "папка": "path",
    "путь": "path",
    "результат": "output",
}


def select_execution_mode(requested: str, goal: str) -> str:
    if requested == "goal":
        return "goal"
    if requested == "full":
        return "full"
    if requested == "adaptive":
        return "adaptive"
    if requested == "fast":
        return "fast"
    normalized = goal.casefold()
    if normalized.strip() in {"", "task"}:
        return "full"
    if adaptive_default_is_accepted():
        return "adaptive"
    if any(token in normalized for token in FULL_HINTS):
        return "full"
    return "fast"


def adaptive_default_is_accepted(path: Path = ADAPTIVE_ACCEPTANCE) -> bool:
    try:
        decision = load_json(path)
        expected_policy = "sha256:" + hashlib.sha256((ROOT / ".agent-role-policy.yaml").read_bytes()).hexdigest()
        expected_compiler = "sha256:" + hashlib.sha256(
            (ROOT / "ai_harness" / "planning" / "workflow_compiler.py").read_bytes()
        ).hexdigest()
        dataset_path = ROOT / "evals" / "datasets" / "adaptive_execution" / "golden_tasks_v1.json"
        expected_dataset = "sha256:" + hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        report_path = Path(str(decision.get("report_path", "")))
        report_path.resolve().relative_to(RUNS.resolve())
        expected_report = (
            "sha256:" + hashlib.sha256(report_path.read_bytes()).hexdigest()
            if report_path.is_file()
            else ""
        )
        report = load_json(report_path) if report_path.is_file() else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    return bool(
        isinstance(decision, dict)
        and decision.get("status") == "pass"
        and decision.get("adaptive_default_allowed") is True
        and decision.get("evidence_kind") == "paired_authoritative_runs"
        and int(decision.get("dataset_cases", 0) or 0) >= 50
        and decision.get("role_policy_fingerprint") == expected_policy
        and decision.get("dataset_fingerprint") == expected_dataset
        and decision.get("report_fingerprint") == expected_report
        and str(decision.get("compiler_version", "")) == COMPILER_VERSION
        and decision.get("compiler_fingerprint") == expected_compiler
        and isinstance(report, dict)
        and report.get("status") == "pass"
        and report.get("adaptive_default_allowed") is True
        and report.get("evidence_kind") == "paired_authoritative_runs"
    )


def accepted_adaptive_eval_success_rate(path: Path = ADAPTIVE_ACCEPTANCE) -> float | None:
    if not adaptive_default_is_accepted(path):
        return None
    try:
        decision = load_json(path)
        report_path = Path(str(decision.get("report_path", "")))
        report = load_json(report_path)
        value = report.get("metrics", {}).get("adaptive_success_rate")
        return float(value) if isinstance(value, (int, float)) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def requested_paths_from_goal(goal: str) -> list[str]:
    """Extract only explicit repository-like paths; analysis is conservative otherwise."""

    candidates = re.findall(
        r"(?<![\w.-])(?:[\w.-]+/)+[\w.@+-]+(?:\.[A-Za-z0-9_-]+)?",
        goal,
    )
    return sorted({value.strip("`'\".,:;()[]{}") for value in candidates if value.strip()})


def available_deterministic_tools(repository: Path, project_profile: str) -> list[str]:
    """Return capabilities that can actually run in this checkout."""

    capabilities = {"diff_size"} if shutil.which("git") else set()
    capabilities.add("changed_symbols")
    capabilities.add("secret_scan")
    if (
        project_profile in {"agent_workspace", "django"} and shutil.which("pip-audit")
    ) or (
        project_profile == "nextjs_web" and (shutil.which("npm") or shutil.which("bun"))
    ):
        capabilities.add("dependency_audit")
    if shutil.which("pytest") or (repository / "tests").is_dir():
        capabilities.add("tests")
    if shutil.which("ruff"):
        capabilities.update(("format", "lint"))
    if shutil.which("mypy") or shutil.which("pyright"):
        capabilities.add("types")
    if project_profile == "nextjs_web":
        if shutil.which("bun") or shutil.which("npm"):
            capabilities.update(("format", "lint", "types", "tests"))
    if project_profile == "django" and (repository / "manage.py").is_file():
        capabilities.update(("tests", "django_deploy_check"))
    return sorted(capabilities)


def historical_failure_signals(repository: Path, project_profile: str, *, limit: int = 20) -> list[str]:
    """Read bounded, repository-matching failure facts from prior authoritative runs."""

    signals: list[str] = []
    for workflow_path in sorted(RUNS.glob("*/workflow.json"), reverse=True)[:100]:
        try:
            value = load_json(workflow_path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        try:
            same_repository = Path(str(value.get("repository", ""))).resolve() == repository.resolve()
        except (OSError, RuntimeError):
            same_repository = False
        if not same_repository or str(value.get("project_profile", "")) != project_profile:
            continue
        failure_kind = str(value.get("failure_kind", "")).strip()
        if failure_kind:
            signals.append(f"workflow:{failure_kind}")
        for checkpoint in reversed(value.get("roles", [])):
            if not isinstance(checkpoint, dict):
                continue
            result = checkpoint.get("result", {})
            if isinstance(result, dict) and result.get("status") in {"failed", "blocked"}:
                signals.append(f"role:{checkpoint.get('role', 'unknown')}:{result.get('status')}")
                break
        if len(signals) >= limit:
            break
    return list(dict.fromkeys(signals))[:limit]


def compile_adaptive_execution_plan(
    *,
    task_id: str,
    goal: str,
    project_profile: str,
    requested_paths: list[str],
    repository: Path | None = None,
    historical_failures: list[str] | None = None,
) -> dict[str, Any]:
    checkout = (repository or ROOT).resolve()
    analysis = TaskAnalyzer().analyze(
        goal,
        repository_profile=project_profile,
        project_type=project_profile,
        requested_paths=requested_paths,
        deterministic_tools=available_deterministic_tools(checkout, project_profile),
        historical_failures=(
            historical_failures
            if historical_failures is not None
            else historical_failure_signals(checkout, project_profile)
        ),
    )
    policy = RolePolicy.load(ROOT / ".agent-role-policy.yaml")
    return WorkflowCompiler(policy).compile(
        analysis,
        task_id=task_id,
        mode="adaptive",
        project_profile=project_profile,
    ).as_dict()


def adaptive_parallel_companions(
    state: dict[str, Any],
    *,
    role: str,
    completed_roles: list[str],
) -> tuple[str, ...]:
    """Select only supported deterministic read-only peers from the current DAG frontier."""

    if state.get("effective_mode") != "adaptive":
        return ()
    plan_path = Path(str(state.get("execution_plan_path", "")))
    plan = load_json(plan_path) if plan_path.is_file() else {}
    if not isinstance(plan, dict):
        return ()
    nodes = {
        str(node.get("id", "")): node
        for node in plan.get("nodes", [])
        if isinstance(node, dict)
    }
    completed = set(completed_roles)
    for raw_group in plan.get("parallel_groups", []):
        if not isinstance(raw_group, list) or role not in raw_group:
            continue
        companions: list[str] = []
        for node_id in raw_group:
            node = nodes.get(str(node_id), {})
            candidate = str(node.get("role", node_id))
            dependencies = {
                str(value)
                for value in node.get("dependencies", [])
                if isinstance(value, str)
            }
            if (
                candidate != role
                and candidate not in completed
                and node.get("execution_kind") == "harness_stage"
                and node.get("read_only") is True
                and dependencies <= completed
            ):
                companions.append(candidate)
        return tuple(companions)
    return ()


def adaptive_node(state: dict[str, Any], role: str) -> dict[str, Any]:
    if state.get("effective_mode") != "adaptive":
        return {}
    plan_path = Path(str(state.get("execution_plan_path", "")))
    plan = load_json(plan_path) if plan_path.is_file() else {}
    if not isinstance(plan, dict):
        return {}
    return next(
        (
            dict(node)
            for node in plan.get("nodes", [])
            if isinstance(node, dict) and str(node.get("role", node.get("id", ""))) == role
        ),
        {},
    )


def planned_execution_kind(state: dict[str, Any], role: str) -> str:
    return str(adaptive_node(state, role).get("execution_kind", role_contract(role).get("execution_kind", "llm_role")))


def security_required_checks(state: dict[str, Any]) -> set[str]:
    node_checks = adaptive_node(state, "security-agent").get("deterministic_checks", [])
    checks = {str(value) for value in node_checks if isinstance(value, str)}
    analysis = state.get("task_analysis", {})
    if isinstance(analysis, dict):
        if analysis.get("task_class") == "dependency":
            checks.add("dependency_audit")
        if analysis.get("requires_security_review") is True:
            checks.add("secret_scan")
    return checks


def make_run_id(workflow: str) -> str:
    return datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S.%fZ-{workflow}")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_trace(layout: RunLayout, event: dict[str, Any]) -> None:
    with (layout.raw_events / "workflow.jsonl").open("a", encoding="utf-8") as trace:
        trace.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False) + "\n")


def role_prompt(role: str) -> str:
    prompt_file = PROMPT_FILES.get(role)
    if not prompt_file:
        return ""
    path = PROMPTS / prompt_file
    return path.read_text(encoding="utf-8") if path.exists() else ""


def completed_result(summary: str, next_action: str = "continue") -> dict[str, Any]:
    return {
        "status": "completed",
        "next_action": next_action,
        "summary": summary,
        "artifacts_created": [],
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def blocked_result(summary: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "next_action": "blocked",
        "summary": summary,
        "artifacts_created": [],
        "blockers": blockers,
        "warnings": [],
        "tokens_used": 0,
        "duration_ms": 0,
    }


def persist_control_failure(
    layout: RunLayout,
    state: dict[str, Any],
    *,
    role: str,
    stage: str,
    kind: str,
    error_type: str,
    message: str,
) -> FailureRecord:
    """Persist a structured record for harness failures outside a role runtime."""
    failure = FailureRecord.create(
        run_id=layout.run_id,
        task_id=str(state.get("task_id", "task")),
        role=role,
        stage=stage,
        kind=kind,
        error_type=error_type,
        message=message,
        retryable=kind in {"transient", "runtime_failure", "tool_failure", "internal_error"},
        repairable=kind in {"invalid_output", "verification_failure"},
        checkpoint=f"before_{role.replace('-', '_')}",
    )
    persist_failure(layout.root, failure)
    state["failure_id"] = failure.failure_id
    state["failure_kind"] = failure.kind
    return failure


def role_checkpoint(
    *,
    run_dir: Path,
    run_id: str,
    role: str,
    state_name: str,
    attempt: int,
    worktree: Path,
    input_fingerprint: str,
    result: dict[str, Any] | None = None,
) -> None:
    output_fingerprint = ""
    artifacts: list[str] = []
    if result is not None:
        output_fingerprint = "sha256:" + hashlib.sha256(
            json.dumps(result, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
        ).hexdigest()
        artifacts = [str(item) for item in result.get("artifacts_created", []) if isinstance(item, str)]
    write_checkpoint(
        run_dir,
        RoleCheckpoint(
            run_id=run_id,
            role=role,
            state=state_name,
            attempt=max(1, attempt),
            worktree=str(worktree.resolve()),
            input_fingerprint=input_fingerprint,
            output_fingerprint=output_fingerprint,
            artifacts=artifacts,
        ),
    )


def schedule_role_recovery(
    *,
    layout: RunLayout,
    state: dict[str, Any],
    role: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    policy = load_recovery_policy()
    hint = result.get("_failure", {})
    preliminary = classify_failure(
        None,
        None,
        state,
        result,
        layout.artifacts,
        run_id=layout.run_id,
        task_id=str(state.get("task_id", "task")),
        role=role,
        stage="runtime_execute",
    )
    configured = policy.for_kind(preliminary.kind)
    recovery = state.get("recovery", {})
    if not isinstance(recovery, dict):
        recovery = {}
    by_kind = recovery.get("attempts_by_kind", {})
    if not isinstance(by_kind, dict):
        by_kind = {}
    attempt = int(by_kind.get(preliminary.kind, 0) or 0) + 1
    if isinstance(hint, dict) and int(hint.get("repair_attempts", 0) or 0) >= configured.max_attempts:
        attempt = max(attempt, configured.max_attempts + 1)
    checkpoint = f"before_{role.replace('-', '_')}"
    failure = classify_failure(
        None,
        None,
        state,
        result,
        layout.artifacts,
        run_id=layout.run_id,
        task_id=str(state.get("task_id", "task")),
        role=role,
        stage="runtime_execute",
        checkpoint=checkpoint,
        attempt=attempt,
        max_attempts=configured.max_attempts,
    )
    decision = RecoveryCoordinator().decide(failure, state, policy)
    persist_failure(layout.root, failure)
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
            "resume_from": checkpoint,
            "failure_id": failure.failure_id,
            "failure_kind": failure.kind,
            "recovery_action": decision.action,
            "recovery_reason": decision.reason,
            "retry_after_seconds": decision.delay_seconds,
            "recovery": recovery,
        }
    )
    return {"failure": failure.as_json(), "decision": decision.as_json()}


def artifact_result(summary: str, artifacts_created: list[str], next_action: str = "continue") -> dict[str, Any]:
    return {
        "status": "completed",
        "next_action": next_action,
        "summary": summary,
        "artifacts_created": artifacts_created,
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def role_budget_tokens(result: dict[str, Any]) -> int:
    """Return incremental token usage, excluding input served from cache."""
    return incremental_tokens(result)


def resume_runtime_command(
    stored_runtime: dict[str, Any],
    *,
    runtime_command: str = "",
    adapter_command: str = "",
) -> str:
    """Reuse fixture commands only; production runtimes must reload trusted config."""
    explicit = runtime_command or adapter_command
    if explicit:
        return explicit
    if stored_runtime.get("production") is True or stored_runtime.get("provider") == "codex-cli":
        return ""
    return str(stored_runtime.get("command", ""))


def awaiting_approval_result(summary: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "status": "awaiting_approval",
        "next_action": "awaiting_approval",
        "summary": summary,
        "artifacts_created": [],
        "blockers": blockers,
        "warnings": [],
        "tokens_used": 0,
        "duration_ms": 0,
    }


def completed_role_result(state: dict[str, Any], role: str) -> dict[str, Any] | None:
    """Return the latest successful result for checkpoint-only approval resume."""

    for checkpoint_entry in reversed(state.get("roles", [])):
        if not isinstance(checkpoint_entry, dict) or checkpoint_entry.get("role") != role:
            continue
        result = checkpoint_entry.get("result")
        if isinstance(result, dict) and result.get("status") == "completed":
            return result
    return None


def set_attention(
    state: dict[str, Any],
    *,
    summary: str,
    details: list[str],
    role: str,
    action: str,
    question: Any = None,
    stop_if_previously_answered: bool = False,
) -> bool:
    """Persist one concise, user-facing explanation for a paused workflow."""

    normalized = list(
        dict.fromkeys(str(item).strip() for item in details if str(item).strip())
    )
    if not normalized:
        normalized = [summary]
    normalized_question = normalize_question(question)
    requirement = semantic_requirement(summary, normalized_question)
    fingerprint = attention_fingerprint(
        role=role,
        summary=summary,
        question_id=str(normalized_question.get("id", "")),
    )
    candidate_fingerprints = {
        fingerprint,
        attention_fingerprint(role=role, summary=summary),
    }
    matched_requirement = (
        matching_requirement(state, requirement) if stop_if_previously_answered else None
    )
    if stop_if_previously_answered and (
        matched_requirement is not None
        or question_was_answered(state, candidate_fingerprints)
    ):
        matched_id = str(
            matched_requirement.get("requirement_id", requirement["requirement_id"])
            if isinstance(matched_requirement, dict)
            else requirement["requirement_id"]
        )
        repeated_details = [
            f"Repeated question: {summary}",
            f"Matched missing requirement: {matched_id}",
            "This requirement was already requested or closed; inspect the run-bound answer, SDK thread, and role context before retrying.",
        ]
        state["attention"] = {
            "required": True,
            "summary": "The system repeated a previously answered question and was stopped.",
            "details": repeated_details,
            "role": role,
            "action": "fix_then_retry",
            "fingerprint": fingerprint,
            "repeated_question": True,
            "repeated_requirement": True,
            "requirement": requirement,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        state["blockers"] = repeated_details
        return True
    attention = {
        "required": True,
        "summary": summary,
        "details": normalized,
        "role": role,
        "action": action,
        "fingerprint": fingerprint,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if normalized_question:
        attention["question"] = normalized_question
    if stop_if_previously_answered:
        attention["requirement"] = requirement
        requests = state.get("missing_requirement_requests", [])
        if not isinstance(requests, list):
            requests = []
        requests.append(
            {
                **requirement,
                "role": role,
                "fingerprint": fingerprint,
                "summary": summary,
                "requested_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        state["missing_requirement_requests"] = requests[-50:]
    state["attention"] = attention
    state["blockers"] = normalized
    return False


def normalize_question(value: Any) -> dict[str, Any]:
    """Bound a model-provided question to the small dashboard interaction contract."""

    if not isinstance(value, dict):
        return {}
    question_id = re.sub(r"[^a-z0-9_]+", "_", str(value.get("id", "")).casefold()).strip("_")[:80]
    raw_options = value.get("options", [])
    if not question_id or not isinstance(raw_options, list):
        return {}
    options: list[dict[str, Any]] = []
    seen_values: set[str] = set()
    for raw_option in raw_options:
        if not isinstance(raw_option, dict):
            continue
        label = str(raw_option.get("label", "")).strip()[:120]
        option_value = str(raw_option.get("value", label)).strip()[:500]
        if not label or not option_value or option_value in seen_values:
            continue
        seen_values.add(option_value)
        options.append(
            {
                "label": label,
                "description": str(raw_option.get("description", "")).strip()[:500],
                "value": option_value,
                "recommended": bool(raw_option.get("recommended", False)),
                "requires_input": bool(raw_option.get("requires_input", False)),
            }
        )
        if len(options) == 3:
            break
    if len(options) < 2:
        return {}
    recommended = [option for option in options if option["recommended"]]
    if recommended:
        selected = recommended[0]
        options = [selected, *[option for option in options if option is not selected]]
    for index, option in enumerate(options):
        option["recommended"] = index == 0
    return {
        "id": question_id,
        "requirement": str(value.get("requirement", "")).strip()[:200],
        "options": options,
        "allow_custom": bool(value.get("allow_custom", True)),
    }


def semantic_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    raw_tokens = re.findall(r"[a-zа-яё0-9]+", normalized)
    tokens = {
        QUESTION_TOKEN_ALIASES.get(token, token)
        for token in raw_tokens
        if len(token) > 1 and token not in QUESTION_STOP_WORDS
    }
    return sorted(tokens)


def semantic_requirement(summary: str, question: dict[str, Any]) -> dict[str, Any]:
    explicit = str(question.get("requirement", "")).strip()
    question_id = str(question.get("id", "")).replace("_", " ")
    signatures = [
        semantic_tokens(value)
        for value in (explicit, question_id, summary)
        if value.strip()
    ]
    signatures = [signature for signature in signatures if signature]
    primary = signatures[0] if signatures else ["unspecified"]
    requirement_id = "_".join(primary)[:120]
    aliases = sorted({" ".join(signature) for signature in signatures})
    return {
        "requirement_id": requirement_id,
        "semantic_aliases": aliases,
        "source_question_id": str(question.get("id", "")),
    }


def requirement_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_aliases = left.get("semantic_aliases", [])
    right_aliases = right.get("semantic_aliases", [])
    if not isinstance(left_aliases, list) or not isinstance(right_aliases, list):
        return 0.0
    best = 0.0
    for left_alias in left_aliases:
        left_tokens = set(str(left_alias).split())
        for right_alias in right_aliases:
            right_tokens = set(str(right_alias).split())
            union = left_tokens | right_tokens
            if union:
                best = max(best, len(left_tokens & right_tokens) / len(union))
    return best


def matching_requirement(
    state: dict[str, Any], requirement: dict[str, Any]
) -> dict[str, Any] | None:
    for field in ("closed_requirements", "missing_requirement_requests"):
        entries = state.get(field, [])
        if not isinstance(entries, list):
            continue
        for entry in reversed(entries):
            if not isinstance(entry, dict):
                continue
            if entry.get("requirement_id") == requirement.get("requirement_id"):
                return entry
            if requirement_similarity(entry, requirement) >= 0.6:
                return entry
    return None


def attention_fingerprint(*, role: str, summary: str, question_id: str = "") -> str:
    identity = question_id or re.sub(r"\W+", " ", summary.casefold()).strip()
    value = f"{role.casefold().strip()}\0{identity}"
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def question_was_answered(state: dict[str, Any], fingerprints: set[str]) -> bool:
    history = state.get("attention_history", [])
    if not isinstance(history, list):
        return False
    for item in reversed(history):
        if not isinstance(item, dict) or item.get("resolution") != "answer_recorded":
            continue
        previous_question = item.get("question", {})
        previous_question_id = (
            str(previous_question.get("id", ""))
            if isinstance(previous_question, dict)
            else ""
        )
        previous_fingerprints = {
            str(item.get("fingerprint", "")),
            attention_fingerprint(
                role=str(item.get("role", "")),
                summary=str(item.get("summary", "")),
            ),
            attention_fingerprint(
                role=str(item.get("role", "")),
                summary=str(item.get("summary", "")),
                question_id=previous_question_id,
            ),
        }
        previous_fingerprints.discard("")
        if previous_fingerprints & fingerprints:
            return True
    return False


def role_attention_action(result: dict[str, Any]) -> str:
    """Route deliberate structured questions to informational input."""

    if result.get("status") == "awaiting_approval" or normalize_question(
        result.get("question")
    ):
        return "answer"
    return "fix_then_retry"


def validate_role_result(result: dict[str, Any], role: str) -> list[str]:
    return validate_contract(result, load_json(SCHEMAS / "role_result.schema.json"), f"{role} role_result")


def validate_manifest(path: Path, role: str) -> list[str]:
    data = load_json(path)
    return validate_contract(data, load_json(SCHEMAS / "context_manifest.schema.json"), f"{role} context_manifest")


def project_profile_for(project: str) -> str:
    return project if project in KNOWN_PROJECT_PROFILES else "agent_workspace"


def role_tools(role: str) -> list[str]:
    tools = role_capability(role).get("tools", DEFAULT_ALLOWED_TOOLS)
    return list(tools) if isinstance(tools, list) else list(DEFAULT_ALLOWED_TOOLS)


def role_filesystem_access(role: str) -> str:
    return str(role_capability(role).get("filesystem", "read_only"))


def safe_artifact_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def artifact_bytes(path: Path, *, stop_after: int) -> int:
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            total += candidate.stat().st_size
        except OSError:
            continue
        if total > stop_after:
            return total
    return total


def execute_runtime_observed(
    runtime: Any,
    *,
    run_dir: Path,
    role: str,
    context: Path,
    task: dict[str, Any],
    worktree: Path,
    artifacts: Path,
) -> dict[str, Any]:
    telemetry = safe_telemetry_runtime(run_dir=run_dir, service_name="ai-harness-role-runtime")
    try:
        with telemetry.span("ai_harness.runtime.execute", {"role": role}) as span:
            telemetry.runtime_executions_total.add(1, {"role": role})
            result = runtime.execute(
                role=role,
                context=context,
                task=task,
                worktree=worktree,
                artifacts=artifacts,
            )
            failure = result.get("_failure", {}) if isinstance(result, dict) else {}
            if isinstance(failure, dict) and failure:
                error_type = str(failure.get("error_type", "RuntimeFailure"))
                telemetry.runtime_failures_total.add(1, {"error.type": error_type})
                span.set_attribute("error.type", error_type)
                span.set_status(Status(StatusCode.ERROR))
                if "timeout" in error_type.lower():
                    telemetry.runtime_timeouts_total.add(1, {"error.type": error_type})
                    with telemetry.span("ai_harness.runtime.timeout", {"role": role, "error.type": error_type}):
                        pass
            return result
    finally:
        try:
            telemetry.shutdown()
        except Exception:
            pass


def ensure_project_profile_artifact(artifacts_dir: Path, project_profile: str) -> None:
    path = artifacts_dir / "project_profile.json"
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            data = {}
    profiles = load_yaml(ROOT / ".agent-project-profiles.yaml").get("profiles", {})
    profile = profiles.get(project_profile, {}) if isinstance(profiles, dict) else {}
    quality = profile.get("quality_commands", {}) if isinstance(profile, dict) else {}
    security = profile.get("security_commands", {}) if isinstance(profile, dict) else {}
    required_quality = quality.get("required", []) if isinstance(quality, dict) else []
    required_security = security.get("required", []) if isinstance(security, dict) else []
    frontend = profile.get("frontend_evidence", {}) if isinstance(profile, dict) else {}
    quality_selected = list(data.get("quality_commands_selected", []))
    security_selected = list(data.get("security_commands_selected", []))
    for command in required_quality:
        if command not in quality_selected:
            quality_selected.append(command)
    for command in required_security:
        if command not in security_selected:
            security_selected.append(command)
    data.update(
        {
            "project_profile": project_profile,
            "confidence": data.get("confidence", "high"),
            "reasons": data.get("reasons", ["Selected by the workflow project profile resolver."]),
            "matched_markers": data.get("matched_markers", []),
            "quality_commands_selected": quality_selected,
            "security_commands_selected": security_selected,
            "frontend_evidence_required": bool(
                data.get("frontend_evidence_required", False)
                or (isinstance(frontend, dict) and frontend.get("required") is True)
            ),
            "frontend_dev_command": str(frontend.get("dev_command", "")) if isinstance(frontend, dict) else "",
            "frontend_local_url": str(frontend.get("local_url", "")) if isinstance(frontend, dict) else "",
            "frontend_network_scope": list(frontend.get("network_scope", [])) if isinstance(frontend, dict) and isinstance(frontend.get("network_scope", []), list) else [],
            "warnings": data.get("warnings", []),
        }
    )
    write_json(
        path,
        data,
    )


def ensure_deterministic_role_artifacts(role: str, artifacts_dir: Path, project_profile: str) -> list[str]:
    if role != "planner":
        return []
    ensure_project_profile_artifact(artifacts_dir, project_profile)
    return ["project_profile.json"]


def git_snapshot(repo: Path) -> str:
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return ""
    digest = hashlib.sha256()
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary"],
        cwd=repo,
        text=False,
        capture_output=True,
        check=False,
    )
    if diff.returncode == 0:
        digest.update(diff.stdout)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if untracked.returncode == 0:
        for relative in sorted(line.strip() for line in untracked.stdout.splitlines() if line.strip()):
            digest.update(relative.encode("utf-8"))
            path = repo / relative
            if path.is_file():
                digest.update(path.read_bytes())
    return digest.hexdigest()


def changed_paths(repo: Path) -> list[str]:
    paths: set[str] = set()
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if diff.returncode == 0:
        paths.update(line.strip() for line in diff.stdout.splitlines() if line.strip())
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if untracked.returncode == 0:
        paths.update(line.strip() for line in untracked.stdout.splitlines() if line.strip())
    return sorted(paths)


def changed_line_count(repo: Path) -> int:
    result = subprocess.run(
        ["git", "diff", "--numstat", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    total = 0
    for line in result.stdout.splitlines():
        columns = line.split("\t", 2)
        if len(columns) >= 2 and columns[0].isdigit() and columns[1].isdigit():
            total += int(columns[0]) + int(columns[1])
    return total


def active_repair_iteration(state: dict[str, Any], role: str) -> int:
    if role not in {"implementation-agent", "ci-repair-agent"}:
        return 0
    route = state.get("last_route", {})
    loop = route.get("loop", {}) if isinstance(route, dict) else {}
    if not isinstance(loop, dict):
        return 0
    value = loop.get("iteration", 0)
    return int(value) if isinstance(value, (int, float)) else 0


def prior_role_failed(state: dict[str, Any], role: str) -> bool:
    """Return true only for a failed attempt, not a user-answer continuation."""

    for entry in reversed(state.get("roles", [])):
        if not isinstance(entry, dict) or entry.get("role") != role:
            continue
        result = entry.get("result", {})
        if not isinstance(result, dict):
            return False
        return result.get("status") in {"failed", "blocked"} or isinstance(
            result.get("_failure"), dict
        )
    return False


def previous_execution_profile(state: dict[str, Any], role: str) -> str:
    for entry in reversed(state.get("roles", [])):
        if not isinstance(entry, dict) or entry.get("role") != role:
            continue
        if not role_entry_invoked_model(entry):
            continue
        profile = entry.get("execution_profile", {})
        if isinstance(profile, dict):
            return str(profile.get("execution_profile", ""))
    return ""


def previous_reasoning_effort(state: dict[str, Any], role: str) -> str:
    for entry in reversed(state.get("roles", [])):
        if not isinstance(entry, dict) or entry.get("role") != role:
            continue
        if not role_entry_invoked_model(entry):
            continue
        profile = entry.get("execution_profile", {})
        if isinstance(profile, dict):
            return str(profile.get("reasoning_effort", ""))
    return ""


def execution_settings_invoke_runtime(
    settings: dict[str, Any], *, used_cached_result: bool = False
) -> bool:
    return bool(
        settings.get("execution_profile")
        and not used_cached_result
        and settings.get("terminal_action") != "human_or_dead_letter"
    )


def active_model_escalation_approval_id(state: dict[str, Any], role: str) -> str:
    """Return one exact unused model-escalation grant for the current role."""

    if role not in {"implementation-agent", "ci-repair-agent"}:
        return ""
    override = state.get("approval_override", {})
    scope = override.get("scope", {}) if isinstance(override, dict) else {}
    approval_id = str(override.get("approval_id", "")) if isinstance(override, dict) else ""
    if not (
        approval_id
        and override.get("gate") == role
        and isinstance(scope, dict)
        and scope.get("gate") == role
        and scope.get("model_escalation_role") == role
        and set(str(item) for item in scope.get("actions", []) if isinstance(item, str))
        == {"allow_one_model_escalation", "resume_workflow"}
        and not isinstance(scope.get("additional_attempts"), bool)
        and scope.get("additional_attempts") == 1
        and not isinstance(scope.get("model_escalation_uses"), bool)
        and scope.get("model_escalation_uses") == 1
        and scope.get("model_escalation_fingerprint")
        == model_escalation_fingerprint(state, role)
    ):
        return ""
    if not model_escalation_terminal_state(state, role):
        return ""
    history = state.get("attention_history", [])
    if not isinstance(history, list) or not any(
        isinstance(item, dict)
        and item.get("resolution") == "approval_consumed"
        and item.get("role") == role
        and item.get("summary") == MODEL_ESCALATION_SUMMARY
        and isinstance(item.get("requirement"), dict)
        and item["requirement"].get("requirement_id") == MODEL_ESCALATION_REQUIREMENT
        for item in reversed(history)
    ):
        return ""
    grants = state.get("approval_grants", [])
    if not isinstance(grants, list):
        return ""
    matches = [
        grant
        for grant in grants
        if isinstance(grant, dict)
        and grant.get("approval_id") == approval_id
        and grant.get("gate") == role
        and grant.get("scope") == scope
        and grant.get("reason") == MODEL_ESCALATION_SUMMARY
    ]
    uses = state.get("approval_action_uses", [])
    already_used = isinstance(uses, list) and any(
        isinstance(item, dict)
        and item.get("approval_id") == approval_id
        and item.get("action") == "allow_one_model_escalation"
        for item in uses
    )
    if (
        len(matches) != 1
        or matches[0].get("model_escalation_started_at")
        or already_used
    ):
        return ""
    return approval_id


def mark_model_escalation_started(
    state: dict[str, Any], *, role: str, approval_id: str
) -> bool:
    """Consume the exact approval in durable state before invoking the runtime."""

    if not approval_id or active_model_escalation_approval_id(state, role) != approval_id:
        return False
    started_at = datetime.now(timezone.utc).isoformat()
    matches = [
        grant
        for grant in state.get("approval_grants", [])
        if isinstance(grant, dict) and grant.get("approval_id") == approval_id
    ]
    if len(matches) != 1:
        return False
    matches[0]["model_escalation_role"] = role
    matches[0]["model_escalation_started_at"] = started_at
    uses = state.get("approval_action_uses", [])
    if not isinstance(uses, list):
        uses = []
    uses.append(
        {
            "approval_id": approval_id,
            "action": "allow_one_model_escalation",
            "role": role,
            "started_at": started_at,
        }
    )
    state["approval_action_uses"] = uses
    return True


def consume_model_escalation_approval(
    run_dir: Path,
    state: dict[str, Any],
    *,
    role: str,
    approval_id: str,
) -> bool:
    """Atomically consume one model action across overlapping resume processes."""

    with approval_lock(run_dir):
        durable = read_approval_workflow(run_dir / "workflow.json")
        if active_model_escalation_approval_id(durable, role) != approval_id:
            state.clear()
            state.update(durable)
            return False
        if not mark_model_escalation_started(
            durable,
            role=role,
            approval_id=approval_id,
        ):
            state.clear()
            state.update(durable)
            return False
        write_approval_workflow(run_dir / "workflow.json", durable)
        state.clear()
        state.update(durable)
        return True


def active_budget_approval_override(state: dict[str, Any], role: str) -> bool:
    """Do not treat risk, repair, or model approvals as adaptive budget authority."""

    override = state.get("approval_override", {})
    scope = override.get("scope", {}) if isinstance(override, dict) else {}
    approval_id = str(override.get("approval_id", "")) if isinstance(override, dict) else ""
    if not (
        approval_id
        and override.get("gate") == role
        and isinstance(scope, dict)
        and set(str(item) for item in scope.get("actions", []) if isinstance(item, str))
        == {"resume_workflow"}
    ):
        return False
    grants = state.get("approval_grants", [])
    return bool(
        isinstance(grants, list)
        and any(
            isinstance(grant, dict)
            and grant.get("approval_id") == approval_id
            and grant.get("gate") == role
            and grant.get("scope") == scope
            and (
                "budget exceeded" in str(grant.get("reason", "")).casefold()
                or "hard execution bound" in str(grant.get("reason", "")).casefold()
            )
            for grant in grants
        )
    )


def model_failure_type(state: dict[str, Any]) -> str:
    route = state.get("last_route", {})
    loop = route.get("loop", {}) if isinstance(route, dict) else {}
    loop_name = str(loop.get("name", "")) if isinstance(loop, dict) else ""
    if loop_name in {"quality_repair", "ci_repair"}:
        return "test_failure"
    failure_kind = str(state.get("failure_kind", ""))
    if failure_kind == "invalid_output":
        return "invalid_output"
    return failure_kind


def validate_role_artifacts(
    *,
    role: str,
    result: dict[str, Any],
    artifacts_dir: Path,
    worktree: Path,
    source_repository: Path,
    source_snapshot_before: str,
    create_task_worktree: bool,
) -> list[str]:
    errors: list[str] = []
    contract = role_contract(role)
    expected = {str(item) for item in contract.get("expected_artifacts", []) if isinstance(item, str)}
    for artifact in result.get("artifacts_created", []):
        if not safe_artifact_name(artifact):
            errors.append(f"{role}: artifacts_created contains unsafe path {artifact!r}")
            continue
        if role in ADAPTER_ROLES and str(artifact) not in expected:
            errors.append(f"{role} cannot claim artifact it does not own: {artifact}")
    for expected in contract.get("expected_artifacts", []):
        if not safe_artifact_name(expected):
            errors.append(f"{role}: expected artifact uses unsafe path {expected!r}")
            continue
        expected_path = artifacts_dir / expected
        if not expected_path.exists():
            errors.append(f"{role} must create run-scoped {expected}")
            continue
        if expected_path.is_file() and expected_path.stat().st_size == 0:
            errors.append(f"{role} must create non-empty run-scoped {expected}")

    artifact_schemas = contract.get("artifact_schemas", {})
    if isinstance(artifact_schemas, dict):
        for artifact, schema_path in artifact_schemas.items():
            if not isinstance(artifact, str) or not isinstance(schema_path, str):
                errors.append(f"{role}: artifact_schemas entries must be string paths")
                continue
            if not safe_artifact_name(artifact):
                errors.append(f"{role}: artifact schema uses unsafe artifact path {artifact!r}")
                continue
            artifact_path = artifacts_dir / artifact
            if not artifact_path.exists():
                continue
            try:
                data = load_json(artifact_path)
                schema_file = Path(schema_path)
                schema = load_json(schema_file if schema_file.is_absolute() else ROOT / schema_file)
                errors.extend(validate_artifact_required(data, contract_section(schema, "artifact"), artifact))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{artifact} is invalid: {exc}")
    if (
        role_filesystem_access(role) == "task_worktree_write"
        and create_task_worktree
        and worktree.resolve() != source_repository.resolve()
    ):
        source_snapshot_after = git_snapshot(source_repository)
        if source_snapshot_after != source_snapshot_before:
            errors.append(f"{role} changed the source repository instead of only the task worktree")
    errors.extend(validate_verifier_artifact(role, artifacts_dir))
    return errors


def validate_verifier_artifact(role: str, artifacts_dir: Path) -> list[str]:
    artifact_names = {
        "security-agent": "security.json",
        "frontend-qa-agent": "frontend_qa.json",
        "architecture-consistency-agent": "architecture_consistency.json",
        "semantic-conflict-agent": "semantic_conflict.json",
        "reviewer": "review.json",
    }
    artifact_name = artifact_names.get(role)
    if artifact_name is None or not (artifacts_dir / artifact_name).exists():
        return []
    try:
        artifact = load_json(artifacts_dir / artifact_name)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"{artifact_name} verifier artifact is invalid: {exc}"]
    verdict = artifact.get("verdict")
    blockers = artifact.get("blockers", [])
    repair_required = artifact.get("repair_required")
    errors: list[str] = []
    if verdict == "works" and (repair_required is not False or blockers):
        errors.append(f"{artifact_name}: works requires repair_required=false and no blockers")
    if verdict == "broken" and (repair_required is not True or not blockers):
        errors.append(f"{artifact_name}: broken requires repair_required=true and blockers")
    if verdict == "unavailable" and not blockers:
        errors.append(f"{artifact_name}: unavailable requires a blocker explaining missing verification")
    if role == "security-agent":
        severity = artifact.get("highest_severity")
        if verdict == "works" and severity not in {"none", "low"}:
            errors.append("security.json: works permits only none or low highest_severity")
        if verdict == "broken" and severity not in {"medium", "high", "critical"}:
            errors.append("security.json: broken requires medium, high, or critical highest_severity")
    if role != "frontend-qa-agent" or verdict != "works":
        return errors
    local_url = str(artifact.get("local_url", ""))
    host = (urlparse(local_url).hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        errors.append("frontend_qa.json: works requires a loopback local_url")
    if artifact.get("evidence_collected") is not True:
        errors.append("frontend_qa.json: works requires evidence_collected=true")
    screenshots = artifact.get("screenshots", [])
    if not isinstance(screenshots, list) or not screenshots:
        errors.append("frontend_qa.json: works requires at least one screenshot")
    else:
        for value in screenshots:
            if not isinstance(value, str) or not value.startswith("frontend-evidence/") or not safe_artifact_name(value):
                errors.append(f"frontend_qa.json: unsafe screenshot path {value!r}")
                continue
            if not (artifacts_dir / value).is_file():
                errors.append(f"frontend_qa.json: screenshot is missing: {value}")
    dev_server = artifact.get("dev_server")
    if not isinstance(dev_server, dict) or not {"command", "status"}.issubset(dev_server):
        errors.append("frontend_qa.json: works requires dev_server command and status")
    evidence = artifact.get("evidence", [])
    if not isinstance(evidence, list) or not evidence:
        errors.append("frontend_qa.json: works requires interaction evidence")
    return errors


def next_role_name(current_role: str, result: dict[str, Any]) -> str:
    """Deprecated compatibility helper; authoritative routing lives in workflow_router."""
    index = ROLE_CHAIN.index(current_role)
    return ROLE_CHAIN[index + 1] if index + 1 < len(ROLE_CHAIN) else ""


def workflow_budgets(workflow: str, execution_mode: str = "full") -> dict[str, int]:
    document = load_yaml(ROOT / ".agent-workflows.yaml")
    workflow_config = document.get("workflows", {}).get(workflow, {})
    configured = workflow_config.get("budgets", {})
    defaults = {
        "max_roles": 40,
        "max_repair_iterations": 12,
        "max_duration_seconds": 3600,
        "max_tokens": 300000,
    }
    if isinstance(configured, dict):
        for key in defaults:
            if isinstance(configured.get(key), (int, float)):
                defaults[key] = int(configured[key])
    mode_budgets = workflow_config.get("mode_budgets", {})
    selected = mode_budgets.get(execution_mode, {}) if isinstance(mode_budgets, dict) else {}
    if isinstance(selected, dict):
        for key in defaults:
            if isinstance(selected.get(key), (int, float)):
                defaults[key] = int(selected[key])
    return defaults


def workflow_token_pressure_action(state: dict[str, Any]) -> dict[str, object] | None:
    """Return economy pressure after the soft cumulative token ceiling is crossed."""

    budgets = state.get("budgets", {})
    if not isinstance(budgets, dict):
        return None
    max_tokens = budgets.get("max_tokens")
    tokens_used = state.get("tokens_used")
    if (
        not isinstance(max_tokens, (int, float))
        or max_tokens <= 0
        or not isinstance(tokens_used, (int, float))
        or tokens_used < max_tokens
    ):
        return None
    return {
        "action": BudgetAction.ECONOMY.value,
        "reason": (
            "The soft workflow token ceiling was exceeded; mandatory execution "
            "continues in economy mode."
        ),
        "pressure": round(tokens_used / max_tokens, 6),
        "exhausted_dimensions": ["tokens_used"],
    }


def initial_loops() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "iterations": 0,
            "max_iterations": 3,
            "last_failure_fingerprint": "",
            "last_diff_fingerprint": "",
            "progress_detected": False,
        }
        for name in ("quality_repair", "review_repair", "ci_repair", "frontend_verification_repair")
    }


def git_remote(repo: Path) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def publication_requested(goal: str, repository: Path) -> bool:
    """Return whether this task may enter the publication workflow.

    A local project file grants execution identity only. Publication additionally
    requires a central repository registration, and an explicit local-only user
    instruction always wins over that grant.
    """

    remote = git_remote(repository)
    if not remote or find_by_remote(remote) is None:
        return False
    normalized_goal = " ".join(goal.casefold().split())
    local_only_markers = (
        "без публикац",
        "не публиков",
        "не создавай pr",
        "не создавать pr",
        "только локально",
        "do not publish",
        "don't publish",
        "without publication",
        "no publication",
        "do not create a pr",
        "don't create a pr",
        "local only",
    )
    return not any(marker in normalized_goal for marker in local_only_markers)


def git_ref_sha(repo: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def resolve_registry_record(repository: Path, project: str) -> tuple[RepositoryRecord | None, list[str]]:
    remote = git_remote(repository)
    record = find_by_remote(remote) if remote else None
    if record is None:
        try:
            record = load_local_project_record(repository)
        except ValueError as exc:
            return None, [str(exc)]
    if project and record is None:
        return None, [
            "repository is not centrally registered or locally initialized; run `agent init` first"
        ]
    if project and record is not None:
        expected = record.project_profile if record.source == "local_project_config" else record.repository_id
        if expected != project:
            return record, [f"repository identity is {expected!r}, expected {project!r}"]
    return record, []


def prepare_worktree(
    repository: Path,
    task_id: str,
    project: str,
    run_id: str,
    branch: str,
    base_branch: str,
    create_task_worktree: bool,
    current_branch: bool = False,
) -> tuple[Path, str, str, list[str]]:
    record, errors = resolve_registry_record(repository, project)
    if errors:
        return repository, "", "", errors
    effective_base = record.base_branch if record is not None else base_branch
    effective_branch = branch or f"issue/{slug(task_id)}"
    if current_branch:
        result = use_current_checkout(
            repository,
            task_id,
            effective_branch,
            effective_base,
            run_id,
            runs_dir=RUNS,
            require_clean=True,
        )
        if result.get("execution_status") != "completed":
            return repository, effective_branch, effective_base, [str(item) for item in result.get("errors", [])]
        return repository, effective_branch, effective_base, []
    if not create_task_worktree:
        return repository, effective_branch, effective_base, []
    result = create_worktree(
        repository,
        task_id,
        effective_branch,
        effective_base,
        run_id,
        runs_dir=RUNS,
        worktrees_dir=RUNS.parent / ".agent-worktrees",
    )
    if result.get("execution_status") != "completed":
        return repository, effective_branch, effective_base, [str(item) for item in result.get("errors", [])]
    return Path(str(result["worktree"])), effective_branch, effective_base, []


def build_role_request(
    *,
    run_id: str,
    role: str,
    goal: str,
    repository: Path,
    artifacts_dir: Path,
    context_manifest: Path,
    token_budget: int,
    timeout_seconds: int,
    project_profile: str,
    execution_settings: dict[str, Any],
) -> dict[str, Any]:
    contract = role_contract(role)
    return {
        "run_id": run_id,
        "role": role,
        "goal": goal,
        "repository": str(repository.resolve()),
        "artifacts_dir": str(artifacts_dir.resolve()),
        "context_manifest": str(context_manifest.resolve()),
        "prompt_path": str(contract.get("prompt_path", "")),
        "output_contract": str(contract.get("output_contract", "schemas/role_result.schema.json")),
        "project_profile": project_profile,
        "expected_artifacts": list(contract.get("expected_artifacts", [])),
        "allowed_tools": role_tools(role),
        "filesystem_access": role_filesystem_access(role),
        "execution_profile": str(execution_settings["execution_profile"]),
        "model": str(execution_settings["model"]),
        "reasoning_effort": str(execution_settings["reasoning_effort"]),
        "service_tier": str(execution_settings["service_tier"]),
        "profile_reason": str(execution_settings["profile_reason"]),
        "escalation_level": int(execution_settings["escalation_level"]),
        "token_budget": token_budget,
        "timeout_seconds": timeout_seconds,
    }


def frontend_qa_unavailable_result(artifacts_dir: Path, warnings: list[str]) -> dict[str, Any]:
    existing_artifact = artifacts_dir / "frontend_qa.json"
    if existing_artifact.exists():
        try:
            existing = load_json(existing_artifact)
        except (OSError, json.JSONDecodeError, ValueError):
            existing = {}
        if (
            existing.get("verdict") == "works"
            and not validate_verifier_artifact("frontend-qa-agent", artifacts_dir)
        ):
            result = completed_result(
                "Existing run-scoped frontend QA evidence remains valid.",
                "continue",
            )
            result["artifacts_created"] = []
            return result
    write_json(
        artifacts_dir / "frontend_qa.json",
        {
            "verdict": "unavailable",
            "expected": ["A running local development environment and Playwright interaction evidence."],
            "observed": ["Browser or Playwright capability is unavailable in this runtime."],
            "evidence": [],
            "repair_required": False,
            "evidence_required": True,
            "evidence_collected": False,
            "screenshots": [],
            "console_errors": [],
            "network_errors": [],
            "blockers": warnings,
            "local_url": "",
            "dev_server": {},
            "next_action": "continue",
        },
    )
    result = completed_result("Frontend QA evidence is unavailable in this runtime.", "continue")
    result["artifacts_created"] = ["frontend_qa.json"]
    result["warnings"] = warnings
    return result


def preflight_role_execution(
    *,
    role: str,
    project_profile: str,
    artifacts_dir: Path,
    dry_run: bool,
    repository: Path | None = None,
) -> dict[str, Any] | None:
    outcome = role_tool_preflight(
        role=role,
        allowed_tools=role_tools(role),
        project_profile=project_profile,
        dry_run=dry_run,
        run_dir=artifacts_dir.parent,
        repository=repository,
    )
    if outcome["status"] == "blocked":
        return blocked_result("Role tool preflight failed.", list(outcome["blockers"]))
    if outcome["status"] == "unavailable" and role == "frontend-qa-agent":
        return frontend_qa_unavailable_result(artifacts_dir, list(outcome["warnings"]))
    return None


def answered_image_capability_requirement(artifacts_dir: Path) -> bool:
    """Return whether this run resolved the exact image-capability question."""

    workflow_path = artifacts_dir.parent / "workflow.json"
    human_input_path = artifacts_dir.parent / "human-input.json"
    if not workflow_path.exists() or not human_input_path.exists():
        return False
    try:
        workflow = load_json(workflow_path)
        human_input = load_json(human_input_path)
    except (OSError, ValueError):
        return False

    history = workflow.get("attention_history", [])
    entries = human_input.get("entries", [])
    if not isinstance(history, list) or not isinstance(entries, list):
        return False
    answered_fingerprints = {
        str(item.get("fingerprint", ""))
        for item in history
        if isinstance(item, dict)
        and item.get("role") == "implementation-agent"
        and item.get("resolution") == "answer_recorded"
        and isinstance(item.get("requirement"), dict)
        and item["requirement"].get("requirement_id")
        == "capability_implementation_unavailable"
        and isinstance(item.get("details"), list)
        and any(
            "no image-generation capability" in str(detail).casefold()
            for detail in item["details"]
        )
    }
    answered_fingerprints.discard("")
    return any(
        isinstance(entry, dict)
        and entry.get("requirement_id") == "capability_implementation_unavailable"
        and entry.get("question_fingerprint") in answered_fingerprints
        and bool(str(entry.get("response", "")).strip())
        for entry in entries
    )


def missing_image_capability(goal: str, artifacts_dir: Path) -> str:
    """Return a prompt failure when a task requires image assets the role cannot create."""

    if answered_image_capability_requirement(artifacts_dir):
        return ""

    plan_path = artifacts_dir / "plan.md"
    plan = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    text_value = f"{goal}\n{plan}".casefold()
    image_terms = ("image", "photo", "picture", "картин", "фотограф", "изображен")
    generation_terms = (
        "generate", "create new", "new asset", "distinct", "unique", "six", "each category",
        "сгенер", "создай", "новые", "уникаль", "отдельн", "шесть", "каждой категории", "своя",
    )
    no_generation_terms = (
        "do not generate", "don't generate", "without image generation", "no new image",
        "не генер", "не созда", "без генерац",
    )
    supplied_terms = (
        "supplied", "provided", "attached", "existing",
        "предоставлен", "приложен", "существующ",
    )
    explicit_generation_patterns = (
        r"(?:generate|create new)[^\n.!?;]{0,80}(?:image|photo|picture)",
        r"(?:сгенер|создай)[^\n.!?;]{0,80}(?:картин|фотограф|изображен)",
    )
    segments = [
        segment
        for segment in re.split(r"[\n.!?;]+", text_value)
        if segment.strip()
    ]
    if (
        any(
            any(term in segment for term in image_terms)
            and any(term in segment for term in generation_terms)
            and not any(term in segment for term in no_generation_terms)
            and (
                not any(term in segment for term in supplied_terms)
                or any(re.search(pattern, segment) for pattern in explicit_generation_patterns)
            )
            for segment in segments
        )
        and "image_generation" not in role_tools("implementation-agent")
    ):
        return (
            "The plan requires new distinct image assets, but implementation-agent has no image-generation "
            "capability. Supply the assets or enable an image-generation stage before resuming."
        )
    return ""


def profile_required_commands(project_profile: str, group: str) -> list[str]:
    profiles = load_yaml(ROOT / ".agent-project-profiles.yaml").get("profiles", {})
    profile = profiles.get(project_profile, {}) if isinstance(profiles, dict) else {}
    commands = profile.get(group, {}) if isinstance(profile, dict) else {}
    required = commands.get("required", []) if isinstance(commands, dict) else []
    return [str(command) for command in required if isinstance(command, str) and command.strip()]


def run_bounded_commands(commands: list[str], repository: Path, timeout_seconds: int) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    for command in commands:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            outcomes.append(
                {
                    "command": command,
                    "status": "unavailable",
                    "returncode": 124,
                    "output": f"fast verification exceeded its shared {timeout_seconds}s budget",
                }
            )
            continue
        try:
            argv = shlex.split(command)
            if not argv:
                raise ValueError("empty command")
            completed = subprocess.run(
                argv,
                cwd=repository,
                text=True,
                capture_output=True,
                check=False,
                timeout=remaining,
            )
            output = (completed.stdout or completed.stderr).strip()
            outcomes.append(
                {
                    "command": command,
                    "status": "pass" if completed.returncode == 0 else "fail",
                    "returncode": completed.returncode,
                    "output": output[-2000:],
                }
            )
        except (FileNotFoundError, ValueError) as exc:
            outcomes.append({"command": command, "status": "unavailable", "returncode": 127, "output": str(exc)})
        except subprocess.TimeoutExpired:
            outcomes.append(
                {
                    "command": command,
                    "status": "unavailable",
                    "returncode": 124,
                    "output": f"timed out after {remaining}s",
                }
            )
    return outcomes


def run_deterministic_quality(
    *, goal: str, project_profile: str, repository: Path, artifacts_dir: Path, timeout_seconds: int
) -> dict[str, Any]:
    commands = profile_required_commands(project_profile, "quality_commands")
    checks = run_bounded_commands(commands, repository, min(timeout_seconds, 180))
    changed = changed_paths(repository)
    symbol_names: list[str] = []
    for relative in changed[:100]:
        path = repository / relative
        if path.suffix == ".py" and path.is_file():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            symbol_names.extend(
                f"{relative}:{node.name}"
                for node in ast.walk(tree)
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            )
    checks.extend(
        (
            {
                "command": "git diff --numstat",
                "status": "pass",
                "returncode": 0,
                "output": json.dumps({"changed_files": len(changed), "changed_lines": changed_line_count(repository)}),
            },
            {
                "command": "AST changed-symbol inventory",
                "status": "pass",
                "returncode": 0,
                "output": json.dumps(symbol_names[:500], ensure_ascii=False),
            },
        )
    )
    failed = [item for item in checks if item["status"] == "fail"]
    unavailable = [item for item in checks if item["status"] == "unavailable"]
    overall = "fail" if failed else "warn" if unavailable else "pass"
    warnings = [f"{item['command']}: {item['output']}" for item in unavailable]
    write_json(
        artifacts_dir / "quality.json",
        {
            "task": goal,
            "project_profile": project_profile,
            "overall_status": overall,
            "checks": checks,
            "commands_attempted": commands,
            "focused_tests_passed": not failed,
            "repository_checks_passed": not failed and not unavailable,
            "coverage": "not separately measured by the deterministic quality stage",
            "warnings": warnings,
        },
    )
    result = artifact_result("Deterministic quality checks completed.", ["quality.json"], "continue")
    result["warnings"] = warnings
    if unavailable and not failed:
        result["status"] = "awaiting_approval"
        result["summary"] = "Required deterministic quality checks were unavailable."
        result["blockers"] = warnings
    return result


def run_deterministic_security(
    *,
    project_profile: str,
    repository: Path,
    artifacts_dir: Path,
    timeout_seconds: int,
    required_checks: set[str] | None = None,
) -> dict[str, Any]:
    commands = profile_required_commands(project_profile, "security_commands")
    required = required_checks or set()
    if "secret_scan" in required:
        scanner = ROOT / "scripts" / "security_scan.py"
        commands.append(
            " ".join(
                (
                    shlex.quote(sys.executable),
                    shlex.quote(str(scanner)),
                    "--repo",
                    shlex.quote(str(repository)),
                    "--profile",
                    shlex.quote(project_profile),
                    "--full-repo",
                )
            )
        )
    dependency_scanner_missing = False
    if "dependency_audit" in required:
        if project_profile in {"agent_workspace", "django"} and shutil.which("pip-audit"):
            commands.append("pip-audit")
        elif project_profile == "nextjs_web" and shutil.which("npm"):
            commands.append("npm audit --audit-level=high")
        elif project_profile == "nextjs_web" and shutil.which("bun"):
            commands.append("bun audit")
        else:
            dependency_scanner_missing = True
    checks = run_bounded_commands(commands, repository, min(timeout_seconds, 180))
    if dependency_scanner_missing:
        checks.append(
            {
                "command": "dependency vulnerability scanner",
                "status": "unavailable",
                "returncode": 127,
                "output": "dependency change requires pip-audit, npm audit, or bun audit",
            }
        )
    failed = [item for item in checks if item["status"] == "fail"]
    unavailable = [item for item in checks if item["status"] == "unavailable"]
    blockers = [f"{item['command']}: {item['output']}" for item in [*failed, *unavailable]]
    warnings = [f"{item['command']}: {item['output']}" for item in unavailable]
    verdict = "broken" if failed else "unavailable" if unavailable else "works"
    write_json(
        artifacts_dir / "security.json",
        {
            "verdict": verdict,
            "expected": commands,
            "observed": [f"{item['command']}: {item['status']}" for item in checks],
            "evidence": checks,
            "blockers": blockers,
            "repair_required": bool(failed),
            "status": "fail" if failed else "warn" if unavailable else "pass",
            "highest_severity": "medium" if failed else "none",
            "project_profile": project_profile,
            "findings": [],
            "blocker_ids": [f"security-command-{index + 1}" for index, _item in enumerate(failed)],
            "secret_findings": [],
            "commands_attempted": commands,
            "warnings": warnings,
        },
    )
    result = artifact_result("Deterministic security checks completed.", ["security.json"], "continue")
    result["warnings"] = warnings
    if unavailable and not failed:
        result["status"] = "awaiting_approval"
        result["summary"] = "Required deterministic security checks were unavailable."
        result["blockers"] = blockers
    return result


def run_deterministic_review(
    *, project_profile: str, repository: Path, artifacts_dir: Path
) -> dict[str, Any]:
    quality = load_json(artifacts_dir / "quality.json")
    security = load_json(artifacts_dir / "security.json")
    paths = changed_paths(repository)
    blockers = [
        *(
            [str(item) for item in quality.get("warnings", [])]
            if quality.get("overall_status") == "fail"
            else []
        ),
        *[str(item) for item in security.get("blockers", [])],
    ]
    works = (
        quality.get("overall_status") == "pass"
        and security.get("verdict") == "works"
        and not blockers
    )
    write_json(
        artifacts_dir / "review.json",
        {
            "verdict": "works" if works else "broken",
            "expected": [
                "Required deterministic quality and security gates pass.",
                "The changed-file set remains bounded and reviewable.",
            ],
            "observed": [
                f"quality={quality.get('overall_status', 'missing')}",
                f"security={security.get('verdict', 'missing')}",
                f"changed_files={len(paths)}",
            ],
            "evidence": ["quality.json", "security.json", *paths],
            "blockers": blockers,
            "repair_required": not works,
            "status": "pass" if works else "block",
            "project_profile": project_profile,
            "findings": [],
            "blocker_ids": [
                f"deterministic-review-{index + 1}"
                for index, _item in enumerate(blockers)
            ],
            "policy_violations": [],
            "known_lesson_conflicts": [],
            "warnings": [],
        },
    )
    result = artifact_result(
        "Deterministic review aggregation completed.", ["review.json"], "continue"
    )
    if not works:
        result["status"] = "blocked"
        result["blockers"] = blockers or [
            "deterministic review inputs did not pass"
        ]
    return result


def run_deterministic_orchestrator(
    *, goal: str, project_profile: str, repository: Path, artifacts_dir: Path
) -> dict[str, Any]:
    risk = load_json(artifacts_dir / "risk.json")
    quality = load_json(artifacts_dir / "quality.json")
    security = load_json(artifacts_dir / "security.json")
    review = load_json(artifacts_dir / "review.json")
    paths = changed_paths(repository)
    blockers = [
        *[str(item) for item in security.get("blockers", [])],
        *[str(item) for item in review.get("blockers", [])],
    ]
    quality_passed = quality.get("overall_status") == "pass"
    review_works = review.get("verdict") == "works"
    security_works = security.get("verdict") == "works"
    risk_class = str(risk.get("risk_class", "medium"))
    if not quality_passed or not review_works or not security_works:
        decision = "await_approval"
        execution_status = "blocked"
    elif not paths:
        decision = "no_changes"
        execution_status = "completed"
    elif publication_requested(goal, repository):
        decision = "publish_pr"
        execution_status = "completed"
    else:
        decision = "local_complete"
        execution_status = "completed"
    visual_required = project_profile == "nextjs_web" and any(
        Path(path).suffix.lower() in {".css", ".js", ".jsx", ".ts", ".tsx"} for path in paths
    )
    frontend = load_json(artifacts_dir / "frontend_qa.json") if (artifacts_dir / "frontend_qa.json").exists() else {}
    visual_provided = frontend.get("verdict") == "works"
    warnings = [
        *[str(item) for item in quality.get("warnings", [])],
        *[str(item) for item in security.get("warnings", [])],
    ]
    if decision == "local_complete":
        warnings.append(
            "Publication was skipped because it was not requested or the repository has no central publication grant."
        )
    if visual_required and not visual_provided:
        warnings.append("Visual evidence was not collected; publication must remain draft.")
    write_json(
        artifacts_dir / "verdict.json",
        {
            "decision": decision,
            "execution_status": execution_status,
            "task": goal,
            "project_profile": project_profile,
            "risk_class": risk_class,
            "checks_attempted": True,
            "checks_passed": quality_passed and review_works and security_works,
            "blockers": blockers,
            "warnings": warnings,
            "high_risk_triggers": list(risk.get("high_risk_triggers", [])),
            "protected_paths_touched": list(risk.get("protected_paths_touched", [])),
            "visual_evidence": {"required": visual_required, "provided": visual_provided, "items": []},
            "approval_required_before_publish": decision == "await_approval",
            "approval_required_before_merge": True,
            "reasoning_summary": ["Deterministic aggregation of risk, quality, security, and review."],
            "next_actions": ["Prepare publication inputs."] if decision == "publish_pr" else [],
            "lessons_updated": False,
        },
    )
    return artifact_result("Deterministic workflow verdict recorded.", ["verdict.json"], "continue")


def run_publication(
    *,
    run_id: str,
    repository: Path,
    artifacts_dir: Path,
    dry_run: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/publish_pr.py",
        "--artifacts-dir",
        str(artifacts_dir.resolve()),
        "--run-id",
        run_id,
        "--repo",
        str(repository.resolve()),
    ]
    if dry_run:
        command.append("--dry-run")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        return blocked_result(
            "Publication executor blocked or failed.",
            [(completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"],
        )
    return {
        "status": "completed",
        "next_action": "completed",
        "summary": "Publication executor completed.",
        "artifacts_created": ["publication.json"],
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def high_risk_requested_approval(artifacts_dir: Path) -> bool:
    risk_path = artifacts_dir / "risk.json"
    if not risk_path.exists():
        return False
    try:
        risk = load_json(risk_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    return risk.get("risk_class") == "high"


def run_issue_intake(
    *,
    run_id: str,
    task_id: str,
    goal: str,
    project: str,
    repository: Path,
    worktree: Path,
    branch: str,
    base_branch: str,
    artifacts_dir: Path,
) -> dict[str, Any]:
    intake = {
        "run_id": run_id,
        "task_id": task_id,
        "goal": goal,
        "project": project,
        "repository": str(repository.resolve()),
        "worktree": str(worktree.resolve()),
        "branch": branch,
        "base_branch": base_branch,
    }
    write_json(artifacts_dir / "issue.json", intake)
    return {
        "status": "completed",
        "next_action": "context-compiler",
        "summary": "Deterministic Issue Intake harness stage recorded.",
        "artifacts_created": ["issue.json"],
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def run_context_compiler(
    *,
    run_id: str,
    goal: str,
    project: str,
    worktree: Path,
    artifacts_dir: Path,
    context_dir: Path,
    project_profile: str,
    token_budget: int,
    execution_mode: str,
) -> dict[str, Any]:
    if execution_mode in {"fast", "adaptive"}:
        ensure_project_profile_artifact(artifacts_dir, project_profile)
        profile = load_json(artifacts_dir / "project_profile.json")
        execution_plan = (
            load_json(context_dir.parent / "execution-plan.json")
            if execution_mode == "adaptive"
            else {}
        )
        analysis = execution_plan.get("analysis", {}) if isinstance(execution_plan, dict) else {}
        risk_class = str(analysis.get("risk", "low")) if isinstance(analysis, dict) else "low"
        indicators = list(analysis.get("indicators", [])) if isinstance(analysis, dict) else []
        write_json(
            artifacts_dir / "risk.json",
            {
                "risk_class": risk_class if execution_mode == "adaptive" else "low",
                "reasons": [
                    (
                        "Deterministic Task Analyzer classification; implementation diff and hard gates remain authoritative."
                        if execution_mode == "adaptive"
                        else "Provisional fast-mode classification; the actual diff is checked before verification."
                    )
                ],
                "changed_areas": list(analysis.get("domains", [])) if isinstance(analysis, dict) else [],
                "high_risk_triggers": [
                    value
                    for value in indicators
                    if value in {"auth_change", "permissions_change", "secrets_change", "migration_change"}
                ],
                "protected_paths_touched": [],
                "protected_actions_required": [],
                "autonomy_allowed": {
                    "patch": True,
                    "commit": risk_class != "high",
                    "push": risk_class != "high",
                    "open_pr": risk_class != "high",
                    "update_pr": risk_class != "high",
                    "auto_merge": False,
                    "deploy_staging": False,
                    "deploy_production": False,
                },
            },
        )
        plan = "\n".join(
            [
                "# TASK",
                goal,
                "",
                "# PROJECT_PROFILE",
                project_profile,
                "",
                "# MODE",
                (
                    "Adaptive deterministic plan. Execute only selected DAG nodes and retain every hard gate."
                    if execution_mode == "adaptive"
                    else "Guarded fast path. Inspect only files directly relevant to the request using targeted search."
                ),
                "",
                "# FILES_TO_CHANGE",
                "Determine the smallest relevant file set; stop if the task expands beyond a narrow local patch.",
                "",
                "# DO_NOT_TOUCH",
                "Protected paths, auth, billing, payments, migrations, secrets, production infrastructure, or unrelated files.",
                "",
                "# CHECKS_TO_RUN",
                *[f"- {command}" for command in profile.get("quality_commands_selected", [])],
                "",
                "# DONE_CRITERIA",
                "Implement the requested behavior with focused tests when applicable and a reviewable diff.",
            ]
        )
        (artifacts_dir / "plan.md").write_text(plan + "\n", encoding="utf-8")
        return {
            "status": "completed",
            "next_action": "implementation-agent",
            "summary": (
                "Adaptive context, deterministic risk, and project profile were prepared."
                if execution_mode == "adaptive"
                else "Fast context, provisional risk, and project profile were prepared deterministically."
            ),
            "artifacts_created": ["plan.md", "risk.json", "project_profile.json"],
            "blockers": [],
            "warnings": [],
            "tokens_used": 0,
        }
    contract = role_contract("planner")
    manifest_path = create_context_manifest(
        run_id=run_id,
        role="planner",
        goal=goal,
        repository=worktree,
        artifacts_dir=artifacts_dir,
        context_dir=context_dir,
        project=project,
        project_profile=project_profile,
        token_budget=token_budget,
        allowed_tools=role_tools("planner"),
        previous_roles=["issue-intake", "context-compiler"],
        filesystem_access=role_filesystem_access("planner"),
        prompt_path=str(contract.get("prompt_path", "")),
        output_contract=str(contract.get("output_contract", "")),
        expected_artifacts=list(contract.get("expected_artifacts", [])),
    )
    manifest_errors = validate_manifest(manifest_path, "planner")
    if manifest_errors:
        return blocked_result("Context manifest failed schema validation.", manifest_errors)
    return {
        "status": "completed",
        "next_action": "planner",
        "summary": "Planner context manifest created.",
        "artifacts_created": [],
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def run_publication_prepare(
    *,
    task_id: str,
    project_profile: str,
    repository: Path,
    artifacts_dir: Path,
    base_branch: str,
) -> dict[str, Any]:
    include = changed_paths(repository)
    write_json(
        artifacts_dir / "change_set.json",
        {
            "target_repository": ".",
            "project_profile": project_profile,
            "task_id": task_id,
            "expected_remote": git_remote(repository),
            "include": include,
            "exclude": [],
        },
    )
    title = task_id.replace("-", " ").strip().title() or "Agent workflow update"
    write_json(
        artifacts_dir / "publication_payload.json",
        {
            "title": title,
            "body": "Prepared by the deterministic publication-prepare step from the task worktree diff.",
            "commit_message": title,
            "base_branch": base_branch or "main",
        },
    )
    return artifact_result(
        "Publication inputs prepared from the task worktree diff.",
        ["change_set.json", "publication_payload.json"],
        "publication",
    )


def run_adaptive_read_only_verifier(
    *,
    runtime: Any,
    run_dir: Path,
    state: dict[str, Any],
    role: str,
    goal: str,
    project: str,
    project_profile: str,
    repository: Path,
    artifacts_dir: Path,
    context_dir: Path,
    requests_dir: Path,
    completed_roles: list[str],
    token_budget: int,
    timeout_seconds: int,
    dry_run: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute an independent read-only verifier from the current DAG frontier."""

    kind = planned_execution_kind(state, role)
    plan_path = Path(str(state.get("execution_plan_path", "")))
    plan = load_json(plan_path) if plan_path.is_file() else {}
    budget_pressure = workflow_token_pressure_action(state) is not None
    if isinstance(plan, dict):
        budget_decision = BudgetController.from_plan(plan).assess(
            BudgetUsage.from_state(state),
            mandatory_role=bool(adaptive_node(state, role).get("mandatory", False)),
        )
        if budget_decision.action == BudgetAction.REQUIRE_APPROVAL:
            return awaiting_approval_result(
                "An adaptive hard execution bound is exhausted.",
                ["Mandatory safety gates remain enabled; approval is required to extend the hard bound."],
            ), {"budget_action": budget_decision.as_dict()}
        if budget_decision.action == BudgetAction.SKIP_OPTIONAL:
            result = completed_result("Optional verifier skipped near the task budget ceiling.")
            result["budget_skipped"] = True
            return result, {"budget_action": budget_decision.as_dict()}
        budget_pressure = budget_pressure or budget_decision.action == BudgetAction.ECONOMY
    if role == "quality-runner":
        return run_deterministic_quality(
            goal=goal,
            project_profile=project_profile,
            repository=repository,
            artifacts_dir=artifacts_dir,
            timeout_seconds=timeout_seconds,
        ), {}
    if role == "security-agent":
        scanner_result = run_deterministic_security(
            project_profile=project_profile,
            repository=repository,
            artifacts_dir=artifacts_dir,
            timeout_seconds=timeout_seconds,
            required_checks=security_required_checks(state),
        )
        if kind == "harness_stage" or scanner_result.get("status") != "completed":
            return scanner_result, {}
    contract = role_contract(role)
    context_budgets = plan.get("context_budgets", {}) if isinstance(plan, dict) else {}
    role_budget = int(context_budgets.get(role, token_budget) or token_budget) if isinstance(context_budgets, dict) else token_budget
    manifest_path = create_context_manifest(
        run_id=str(state.get("run_id", run_dir.name)),
        role=role,
        goal=goal,
        repository=repository,
        artifacts_dir=artifacts_dir,
        context_dir=context_dir,
        project=project,
        project_profile=project_profile,
        token_budget=role_budget,
        allowed_tools=role_tools(role),
        previous_roles=completed_roles,
        filesystem_access=role_filesystem_access(role),
        prompt_path=str(contract.get("prompt_path", "")),
        output_contract=str(contract.get("output_contract", "")),
        expected_artifacts=list(contract.get("expected_artifacts", [])),
    )
    errors = validate_manifest(manifest_path, role)
    if errors:
        return blocked_result("Context manifest failed schema validation.", errors), {}
    preflight = preflight_role_execution(
        role=role,
        repository=repository,
        artifacts_dir=artifacts_dir,
        project_profile=project_profile,
        dry_run=dry_run,
    )
    if preflight is not None:
        return preflight, {}
    manifest = load_json(manifest_path)
    analysis = plan.get("analysis", {}) if isinstance(plan, dict) else {}
    profiles = plan.get("model_profiles", {}) if isinstance(plan, dict) else {}
    budgets = plan.get("budgets", {}) if isinstance(plan, dict) else {}
    settings = select_execution_profile(
        role=role,
        goal=goal,
        risk_class=str(analysis.get("risk", state.get("risk_class", "low"))) if isinstance(analysis, dict) else str(state.get("risk_class", "low")),
        changed_files=workflow_changed_files(state, artifacts_dir),
        changed_lines=changed_line_count(repository),
        changed_areas=workflow_changed_areas(state, artifacts_dir),
        repair_iteration=active_repair_iteration(state, role),
        prior_failure=prior_role_failed(state, role),
        previous_profile=previous_execution_profile(state, role),
        previous_reasoning_effort=previous_reasoning_effort(state, role),
        planned_profile=str(profiles.get(role, "")) if isinstance(profiles, dict) else "",
        task_complexity=str(analysis.get("scope", "")) if isinstance(analysis, dict) else "",
        failure_type=model_failure_type(state),
        context_size=int(manifest.get("context_budget", {}).get("used_tokens", 0) or 0),
        eval_success_rate=state.get("adaptive_eval_success_rate"),
        required_capability=(
            "architecture" if role == "architecture-consistency-agent"
            else "deep_review" if role == "semantic-conflict-agent"
            else "security_reasoning" if role == "security-agent"
            else ""
        ),
        repair_count=active_repair_iteration(state, role),
        budget_pressure=budget_pressure,
        max_escalations=int(budgets.get("max_model_escalations", 2) or 2) if isinstance(budgets, dict) else 2,
    )
    if settings.get("terminal_action") == "human_or_dead_letter":
        return awaiting_approval_result(
            "Bounded model escalation is exhausted.",
            ["Human review is required before retrying or dead-lettering this verifier."],
        ), settings
    request = build_role_request(
        run_id=str(state.get("run_id", run_dir.name)),
        role=role,
        goal=goal,
        repository=repository,
        artifacts_dir=artifacts_dir,
        context_manifest=manifest_path,
        token_budget=role_budget,
        timeout_seconds=timeout_seconds,
        project_profile=project_profile,
        execution_settings=settings,
    )
    write_json(requests_dir / f"{role}.json", request)
    return execute_runtime_observed(
        runtime,
        run_dir=run_dir,
        role=role,
        context=manifest_path,
        task=request,
        worktree=repository,
        artifacts=artifacts_dir,
    ), settings


@serialized_run_execution
def run_roles(
    workflow: str = "full_agent_workflow",
    run_id: str = "",
    artifacts_dir: Path | None = None,
    dry_run: bool = False,
    task_id: str = "task",
    goal: str = "",
    project: str = "",
    repository: Path = ROOT,
    branch: str = "",
    base_branch: str = "main",
    adapter_command: str = "",
    token_budget: int = 12000,
    timeout_seconds: int = 1800,
    create_task_worktree: bool = False,
    current_branch: bool = False,
    mode: str = "auto",
    resume: bool = False,
    runtime_provider: str = "",
    runtime_command: str = "",
) -> dict[str, Any]:
    if mode not in EXECUTION_MODES:
        return {
            "run_id": run_id or "invalid-mode",
            "execution_status": "blocked",
            "blockers": ["mode must be auto, adaptive, fast, full, or goal"],
        }
    graph_metadata = task_graph_metadata()
    run_id = run_id or make_run_id(workflow)
    existing_workflow = RUNS / run_id / "workflow.json"
    existing: dict[str, Any] = {}
    if existing_workflow.exists():
        try:
            existing = load_json(existing_workflow)
        except (OSError, json.JSONDecodeError, ValueError):
            existing = {}
    if resume:
        if not existing:
            return {"run_id": run_id, "execution_status": "blocked", "blockers": ["resume state is missing"]}
        if existing.get("execution_status") not in {"resuming", "running"}:
            return {
                **existing,
                "execution_status": "blocked",
                "blockers": [f"run cannot resume from {existing.get('execution_status')!r}"],
            }
        repository = Path(str(existing.get("repository", ""))).resolve()
        task_id = str(existing.get("task_id", task_id))
        goal = str(existing.get("goal", goal or task_id))
        project = str(existing.get("project", project))
        branch = str(existing.get("task_branch", existing.get("branch", branch)))
        base_branch = str(existing.get("base_branch", base_branch))
        workflow = str(existing.get("workflow", workflow))
        mode = str(existing.get("mode", mode))
        fingerprint = str(existing.get("input_fingerprint", ""))
    else:
        repository = repository.resolve()
        goal = goal or task_id
        fingerprint = task_fingerprint(
            task_id=task_id,
            goal=goal,
            repository=repository,
            branch=branch,
            base_branch=base_branch,
            workspace_mode="checkout" if current_branch else "worktree",
            workflow_mode=mode,
        )
        if existing.get("execution_status") == "completed" and existing.get("input_fingerprint") == fingerprint:
            return existing
        if isinstance(existing.get("runtime"), dict) or isinstance(existing.get("executor"), dict):
            if existing.get("input_fingerprint") == fingerprint:
                return existing
            return {
                **existing,
                "execution_status": "blocked",
                "blockers": ["run id already belongs to a different or unfinished workflow; use --resume"],
            }
        duplicate = find_completed_run(RUNS, fingerprint, exclude_run_id=run_id)
        if duplicate is not None:
            return {**duplicate, "deduplicated": True, "duplicate_of": duplicate.get("run_id", "")}
    layout = RunLayout.create(RUNS, run_id)
    try:
        layout.assert_artifacts_dir(artifacts_dir)
    except ValueError as exc:
        state = {
            "run_id": run_id,
            "workflow": workflow,
            "task_id": task_id,
            "execution_status": "blocked",
            "roles": [],
            "loops": initial_loops(),
            "budgets": workflow_budgets(workflow),
            "role_count": 0,
            "tokens_used": 0,
            "blockers": [str(exc)],
            "input_fingerprint": fingerprint,
        }
        write_json(layout.workflow, state)
        record_failure(layout, stage="initialization", code="NON_AUTHORITATIVE_STATE", message=str(exc))
        write_metrics(layout, state)
        return state
    run_dir = layout.root
    run_artifacts = layout.artifacts
    context_dir = layout.context
    requests_dir = layout.requests
    raw_dir = layout.raw_events
    resume_cached_result: dict[str, Any] | None = None
    resume_cached_result_is_replay = False
    resume_cached_runtime_invoked = False
    resume_cached_execution_settings: dict[str, Any] = {}
    checkpoint_problem = ""
    if resume:
        state = existing
        state["role_count"] = accounted_role_count(state.get("roles", []))
        state["tokens_used"] = accounted_tokens_used(state.get("roles", []))
        project_profile = str(state.get("project_profile", project_profile_for(project)))
        worktree = Path(str(state.get("checkout_path", state.get("worktree", "")))).resolve()
        effective_branch = str(state.get("task_branch", state.get("branch", branch)))
        effective_base = str(state.get("base_branch", base_branch))
        current_branch = str(state.get("workspace_mode", "worktree")) in {"checkout", "current_branch"}
        effective_mode = str(state.get("effective_mode", select_execution_mode(mode, goal)))
        stored_runtime = state.get("runtime", state.get("executor", {}))
        if not isinstance(stored_runtime, dict):
            stored_runtime = {}
        stored_provider = str(stored_runtime.get("provider", ""))
        if not stored_provider and stored_runtime.get("kind") == "codex_cli":
            stored_provider = "codex-cli"
        selected_provider = runtime_provider or stored_provider
        selected_command = resume_runtime_command(
            stored_runtime,
            runtime_command=runtime_command,
            adapter_command=adapter_command,
        )
        setup_errors = []
        if not repository.is_dir() or not worktree.is_dir():
            setup_errors.append("resume repository or checkout_path is missing")
        branch_owner_run_id = str(state.get("branch_owner_run_id", run_id))
        if branch_owner_run_id != run_id:
            setup_errors.append(
                f"task branch is owned by run {branch_owner_run_id!r}, not resumed run {run_id!r}"
            )
        if current_branch and repository.is_dir():
            checkout = inspect_current_checkout(
                repository,
                expected_branch=effective_branch,
                protected_branches={effective_base, "main", "master", "trunk"},
                require_clean=False,
            )
            setup_errors.extend(str(item) for item in checkout.get("errors", []))
        if effective_mode == "adaptive":
            plan_path = Path(str(state.get("execution_plan_path", "")))
            if not plan_path.is_file():
                setup_errors.append("adaptive resume is missing its immutable execution plan")
            else:
                plan_value = load_json(plan_path)
                observed_fingerprint = "sha256:" + hashlib.sha256(
                    json.dumps(plan_value, sort_keys=True, ensure_ascii=True).encode("utf-8")
                ).hexdigest()
                if observed_fingerprint != state.get("execution_plan_fingerprint"):
                    setup_errors.append("adaptive execution plan changed after run creation")
        role = str(state.pop("resume_role", ""))
        if not role:
            last_route = state.get("last_route", {})
            if isinstance(last_route, dict):
                candidate = str(last_route.get("next_role", ""))
                if candidate not in {"", "approval-gate", "blocked"}:
                    role = candidate
        if role not in ROLE_CHAIN:
            setup_errors.append("resume checkpoint role is invalid or missing")
        else:
            try:
                stored_checkpoint = read_checkpoint(run_dir, role)
                if stored_checkpoint is not None:
                    if Path(stored_checkpoint.worktree).resolve() != worktree:
                        raise CheckpointError("checkpoint worktree does not match authoritative workflow")
                    operation = checkpoint_resume_operation(stored_checkpoint)
                    if operation == "validate_output":
                        pending_path = layout.role_results / f"{role}-pending.json"
                        pending = load_json(pending_path)
                        cached = pending.get("result") if isinstance(pending, dict) else None
                        if not isinstance(cached, dict):
                            raise CheckpointError("validation checkpoint is missing cached role output")
                        resume_cached_result = cached
                        resume_cached_runtime_invoked = pending.get("runtime_invoked") is True
                        pending_settings = pending.get("execution_profile", {})
                        if isinstance(pending_settings, dict):
                            resume_cached_execution_settings = pending_settings
                    elif operation == "next_role":
                        last_route = state.get("last_route", {})
                        next_role = str(last_route.get("next_role", "")) if isinstance(last_route, dict) else ""
                        if next_role in ROLE_CHAIN:
                            role = next_role
                        elif next_role == "approval-gate":
                            resume_cached_result = completed_role_result(state, role)
                            resume_cached_result_is_replay = resume_cached_result is not None
            except (CheckpointError, OSError, json.JSONDecodeError, ValueError) as exc:
                checkpoint_problem = str(exc)
                setup_errors.append(checkpoint_problem)
        state["execution_status"] = "running"
        state["resume_count"] = int(state.get("resume_count", 0)) + 1
        state["resumed_at"] = datetime.now(timezone.utc).isoformat()
        create_task_worktree = True
        append_trace(layout, {"event": "workflow_resumed", "role": role})
    else:
        project_profile = project_profile_for(project)
        effective_mode = select_execution_mode(mode, goal)
        selected_provider = runtime_provider
        selected_command = runtime_command or adapter_command
        worktree, effective_branch, effective_base, setup_errors = prepare_worktree(
            repository,
            task_id,
            project,
            run_id,
            branch,
            base_branch,
            create_task_worktree,
            current_branch,
        )
    runtime = None
    try:
        runtime = create_runtime(
            provider=selected_provider,
            command=selected_command,
            timeout_seconds=timeout_seconds,
            raw_output_dir=raw_dir,
        )
    except RuntimeConfigurationError as exc:
        setup_errors.append(str(exc))
    if resume:
        if runtime is not None:
            state["runtime"] = runtime.descriptor.as_json()
            state.pop("executor", None)
    else:
        state = {
            "run_id": run_id,
            "workflow": workflow,
            "task_id": task_id,
            "goal": goal,
            "project": project,
            "project_profile": project_profile,
            "repository": str(repository),
            "worktree": str(worktree.resolve()),
            "workspace_mode": "checkout" if current_branch else "worktree",
            "checkout_path": str(worktree.resolve()),
            "task_branch": effective_branch,
            "base_sha": git_ref_sha(repository, effective_base),
            "branch_owner_run_id": run_id,
            "mode": mode,
            "effective_mode": effective_mode,
            "branch": effective_branch,
            "base_branch": effective_base,
            "dry_run": dry_run,
            "execution_status": "running",
            "roles": [],
            "artifacts_dir": str(run_artifacts),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": 0,
            "role_count": 0,
            "tokens_used": 0,
            "input_fingerprint": fingerprint,
            "runtime": runtime.descriptor.as_json() if runtime is not None else {
                "provider": selected_provider,
                "kind": "runtime_adapter",
                "transport": "invalid",
                "production": False,
                "command": selected_command,
                "api_required": False,
            },
            "base_branch_sha_before": git_ref_sha(repository, effective_base),
            "base_branch_sha_after": "",
            "budgets": workflow_budgets(workflow, effective_mode),
            "adaptive_eval_success_rate": accepted_adaptive_eval_success_rate(),
            "loops": initial_loops(),
            "root_run_id": str(graph_metadata.get("root_run_id") or run_id),
            "parent_run_id": str(graph_metadata.get("parent_run_id", "")),
            "relation": str(graph_metadata.get("relation", "root")),
            "dependency_mode": str(graph_metadata.get("dependency_mode", "none")),
            "spawn_reason": str(graph_metadata.get("spawn_reason", "")),
            "allowed_paths": list(graph_metadata.get("allowed_paths", [])),
            "allowed_child_repositories": list(
                graph_metadata.get("allowed_child_repositories", [str(repository)])
            ),
            "graph_depth": int(graph_metadata.get("graph_depth", 0) or 0),
            "child_budget": dict(graph_metadata.get("child_budget", {})),
            "spawn_fingerprint": str(graph_metadata.get("spawn_fingerprint", "")),
            "repository_max_parallel_tasks": int(
                graph_metadata.get("repository_max_parallel_tasks", 0) or 0
            ),
            "batch_id": str(graph_metadata.get("batch_id", "")),
            "batch_index": int(graph_metadata.get("batch_index", 0) or 0),
        }
        if effective_mode == "adaptive":
            requested_paths = [
                str(value)
                for value in graph_metadata.get("allowed_paths", [])
                if isinstance(value, str) and value
            ] or requested_paths_from_goal(goal)
            execution_plan = compile_adaptive_execution_plan(
                task_id=task_id,
                goal=goal,
                project_profile=project_profile,
                requested_paths=requested_paths,
                repository=repository,
            )
            execution_plan_path = layout.root / "execution-plan.json"
            write_json(execution_plan_path, execution_plan)
            state["execution_plan_path"] = str(execution_plan_path.resolve())
            state["execution_plan_fingerprint"] = "sha256:" + hashlib.sha256(
                json.dumps(execution_plan, sort_keys=True, ensure_ascii=True).encode("utf-8")
            ).hexdigest()
            state["task_analysis"] = dict(execution_plan.get("analysis", {}))
        role = ROLE_CHAIN[0]
        append_trace(layout, {"event": "workflow_started", "workflow": workflow, "task_id": task_id})
    child_budget = state.get("child_budget", {})
    if state.get("parent_run_id") and isinstance(child_budget, dict):
        budgets = state.get("budgets", {})
        if isinstance(budgets, dict):
            for field in ("max_tokens", "max_duration_seconds"):
                value = child_budget.get(field)
                if isinstance(value, int) and value > 0:
                    budgets[field] = min(int(budgets.get(field, value) or value), value)
    write_json(layout.workflow, state)
    write_metrics(layout, state)
    if checkpoint_problem:
        failure = FailureRecord.create(
            run_id=run_id,
            task_id=task_id,
            role=role or "resume",
            stage="checkpoint_resume",
            kind="unrecoverable",
            error_type="CorruptedCheckpoint",
            message=checkpoint_problem,
            retryable=False,
            repairable=False,
            checkpoint=str(state.get("resume_from", "")),
        )
        persist_failure(layout.root, failure)
        state.update(
            {
                "execution_status": "dead_letter",
                "failure_id": failure.failure_id,
                "failure_kind": failure.kind,
                "recovery_action": "dead_letter",
                "recovery_reason": "checkpoint cannot be resumed safely",
                "blockers": [checkpoint_problem],
            }
        )
        write_json(layout.workflow, state)
        write_metrics(layout, state)
        return state
    if setup_errors:
        state["execution_status"] = "blocked"
        set_attention(
            state,
            summary="Task workspace setup needs attention.",
            details=setup_errors,
            role="issue-intake",
            action="fix_then_retry",
        )
        write_json(layout.workflow, state)
        append_trace(layout, {"event": "workflow_blocked", "blockers": setup_errors})
        record_failure(
            layout,
            stage="worktree",
            code="WORKTREE_SETUP_FAILED",
            message="Task worktree setup failed.",
            details=setup_errors,
        )
        persist_control_failure(
            layout, state, role="issue-intake", stage="worktree", kind="tool_failure",
            error_type="WorktreeSetupFailed", message="; ".join(setup_errors),
        )
        write_json(layout.workflow, state)
        write_metrics(layout, state)
        return state

    assert runtime is not None
    runtime_preflight = runtime.preflight(worktree=worktree, timeout_seconds=timeout_seconds)
    runtime_preflight["provider"] = runtime.descriptor.provider
    write_json(layout.root / "runtime-preflight.json", runtime_preflight)
    if runtime_preflight.get("execution_status") != "completed":
        blockers = [str(item) for item in runtime_preflight.get("blockers", [])]
        state["execution_status"] = "blocked"
        set_attention(
            state,
            summary="Configured runtime is unavailable.",
            details=blockers or ["Configured runtime is unavailable."],
            role="issue-intake",
            action="fix_then_retry",
        )
        write_json(layout.workflow, state)
        append_trace(layout, {"event": "workflow_blocked", "blockers": state["blockers"]})
        record_failure(
            layout,
            stage="runtime-preflight",
            code="RUNTIME_PREFLIGHT_BLOCKED",
            message="Configured runtime preflight failed.",
            details=state["blockers"],
        )
        persist_control_failure(
            layout, state, role="issue-intake", stage="runtime-preflight", kind="runtime_failure",
            error_type="RuntimePreflightBlocked", message="; ".join(state["blockers"]),
        )
        write_json(layout.workflow, state)
        write_metrics(layout, state)
        return state
    prior_roles = [
        str(item.get("role", ""))
        for item in state.get("roles", [])
        if isinstance(item, dict) and isinstance(item.get("role"), str)
    ]
    completed_roles = [
        str(item.get("role", ""))
        for item in state.get("roles", [])
        if isinstance(item, dict)
        and isinstance(item.get("result"), dict)
        and item["result"].get("status") == "completed"
    ]
    role_visits: dict[str, int] = {
        name: prior_roles.count(name) for name in set(prior_roles)
    }
    state["role_count"] = accounted_role_count(state.get("roles", []))
    write_json(layout.workflow, state)
    source_snapshot_before = git_snapshot(repository)
    guard = 0
    elapsed_before_resume = int(state.get("elapsed_seconds", 0) or 0)
    workflow_started = time.monotonic()
    while role:
        guard += 1
        role_visits[role] = role_visits.get(role, 0) + 1
        role_started = time.monotonic()
        state["current_role"] = role
        state["resume_role"] = role
        background = child_result_context(state)
        role_goal = (
            f"{goal}\n\n{background}"
            if background and role in {"implementation-agent", "ci-repair-agent"}
            else goal
        )
        write_json(layout.workflow, state)
        if resume_cached_result is None:
            role_checkpoint(
                run_dir=run_dir,
                run_id=run_id,
                role=role,
                state_name="role_pending",
                attempt=role_visits[role],
                worktree=worktree,
                input_fingerprint=fingerprint,
            )
            role_checkpoint(
                run_dir=run_dir,
                run_id=run_id,
                role=role,
                state_name="role_running",
                attempt=role_visits[role],
                worktree=worktree,
                input_fingerprint=fingerprint,
            )
        artifact_contents_before = file_contents_snapshot(run_artifacts)
        artifact_snapshot_before = file_snapshot(run_artifacts)
        role_repo_snapshot_before = git_snapshot(worktree)
        max_roles = int(state.get("budgets", {}).get("max_roles", 40))
        effective_mode = str(state.get("effective_mode", effective_mode))
        used_cached_result = False
        cached_result_replay = False
        runtime_invoked = False
        execution_settings: dict[str, Any] = {}
        parallel_role_results: dict[str, dict[str, Any]] = {}
        parallel_role_settings: dict[str, dict[str, Any]] = {}
        role_token_budget = token_budget
        budget_skipped = False
        security_llm_required = (
            role == "security-agent"
            and effective_mode == "adaptive"
            and planned_execution_kind(state, role) == "llm_role"
        )
        security_preflight: dict[str, Any] | None = None
        if security_llm_required and resume_cached_result is None:
            security_preflight = run_deterministic_security(
                project_profile=project_profile,
                repository=worktree,
                artifacts_dir=run_artifacts,
                timeout_seconds=timeout_seconds,
                required_checks=security_required_checks(state),
            )
        if effective_mode == "adaptive" and resume_cached_result is None:
            plan_path = Path(str(state.get("execution_plan_path", "")))
            execution_plan = load_json(plan_path) if plan_path.is_file() else {}
            context_budgets = (
                execution_plan.get("context_budgets", {})
                if isinstance(execution_plan, dict)
                else {}
            )
            configured_role_budget = (
                context_budgets.get(role) if isinstance(context_budgets, dict) else None
            )
            if isinstance(configured_role_budget, int) and configured_role_budget >= 256:
                role_token_budget = configured_role_budget
            if isinstance(execution_plan, dict):
                state["elapsed_seconds"] = elapsed_before_resume + int(time.monotonic() - workflow_started)
                budget_override = (
                    active_budget_approval_override(state, role)
                    and isinstance(state.get("budget_action"), dict)
                    and state["budget_action"].get("action") == BudgetAction.REQUIRE_APPROVAL.value
                )
                if budget_override:
                    state.pop("approval_override", None)
                    state["budget_action"] = {
                        "action": BudgetAction.CONTINUE.value,
                        "reason": "A scoped one-role budget override was consumed.",
                        "pressure": 1.0,
                        "exhausted_dimensions": [],
                    }
                else:
                    budget_decision = BudgetController.from_plan(execution_plan).assess(
                        BudgetUsage.from_state(state),
                        mandatory_role=bool(adaptive_node(state, role).get("mandatory", False)),
                    )
                    state["budget_action"] = budget_decision.as_dict()
                    soft_workflow_pressure = workflow_token_pressure_action(state)
                    if (
                        soft_workflow_pressure is not None
                        and budget_decision.action == BudgetAction.CONTINUE
                    ):
                        state["budget_action"] = soft_workflow_pressure
                write_json(layout.workflow, state)
        if resume_cached_result is not None:
            result = resume_cached_result
            resume_cached_result = None
            used_cached_result = True
            cached_result_replay = resume_cached_result_is_replay
            resume_cached_result_is_replay = False
            runtime_invoked = resume_cached_runtime_invoked
            resume_cached_runtime_invoked = False
            execution_settings = resume_cached_execution_settings
            resume_cached_execution_settings = {}
        elif (
            effective_mode == "adaptive"
            and state.get("budget_action", {}).get("action") == BudgetAction.REQUIRE_APPROVAL.value
        ):
            result = awaiting_approval_result(
                "An adaptive hard execution bound is exhausted.",
                ["Mandatory safety gates remain enabled; approval is required to extend the hard bound."],
            )
        elif (
            effective_mode == "adaptive"
            and state.get("budget_action", {}).get("action") == BudgetAction.SKIP_OPTIONAL.value
            and not bool(adaptive_node(state, role).get("mandatory", False))
        ):
            budget_skipped = True
            state.setdefault("budget_skipped_roles", []).append(role)
            result = completed_result("Optional role skipped under soft task cost pressure.")
        elif security_preflight is not None and security_preflight.get("status") != "completed":
            result = security_preflight
        elif guard > max_roles:
            result = blocked_result("Workflow routing exceeded the safety limit.", ["dynamic routing loop detected"])
        elif role == "issue-intake":
            result = run_issue_intake(
                run_id=run_id,
                task_id=task_id,
                goal=goal,
                project=project,
                repository=repository,
                worktree=worktree,
                branch=effective_branch,
                base_branch=effective_base,
                artifacts_dir=run_artifacts,
            )
        elif role == "context-compiler":
            result = run_context_compiler(
                run_id=run_id,
                goal=goal,
                project=project,
                worktree=worktree,
                artifacts_dir=run_artifacts,
                context_dir=context_dir,
                project_profile=project_profile,
                token_budget=token_budget,
                execution_mode=effective_mode,
            )
        elif role == "publication-prepare":
            result = run_publication_prepare(
                task_id=task_id,
                project_profile=project_profile,
                repository=worktree,
                artifacts_dir=run_artifacts,
                base_branch=effective_base,
            )
        elif role == "publication":
            preflight_result = preflight_role_execution(
                role=role,
                project_profile=project_profile,
                artifacts_dir=run_artifacts,
                dry_run=dry_run,
                repository=worktree,
            )
            result = preflight_result or run_publication(
                run_id=run_id,
                repository=worktree,
                artifacts_dir=run_artifacts,
                dry_run=dry_run,
                timeout_seconds=timeout_seconds,
            )
        elif role == "quality-runner":
            companions = adaptive_parallel_companions(
                state,
                role=role,
                completed_roles=completed_roles,
            )
            if companions:
                with ThreadPoolExecutor(max_workers=1 + len(companions), thread_name_prefix="adaptive-verifier") as executor:
                    quality_future = executor.submit(
                        run_deterministic_quality,
                        goal=goal,
                        project_profile=project_profile,
                        repository=worktree,
                        artifacts_dir=run_artifacts,
                        timeout_seconds=timeout_seconds,
                    )
                    verifier_futures = {
                        companion: executor.submit(
                            run_adaptive_read_only_verifier,
                            runtime=runtime,
                            run_dir=run_dir,
                            state=dict(state),
                            role=companion,
                            goal=goal,
                            project=project,
                            project_profile=project_profile,
                            repository=worktree,
                            artifacts_dir=run_artifacts,
                            context_dir=context_dir,
                            requests_dir=requests_dir,
                            completed_roles=list(completed_roles),
                            token_budget=token_budget,
                            timeout_seconds=timeout_seconds,
                            dry_run=dry_run,
                        )
                        for companion in companions
                    }
                    result = quality_future.result()
                    for companion, future in verifier_futures.items():
                        companion_result, companion_settings = future.result()
                        parallel_role_results[companion] = companion_result
                        parallel_role_settings[companion] = companion_settings
                        if companion_result.get("budget_skipped") is True:
                            state.setdefault("budget_skipped_roles", []).append(companion)
            else:
                result = run_deterministic_quality(
                    goal=goal,
                    project_profile=project_profile,
                    repository=worktree,
                    artifacts_dir=run_artifacts,
                    timeout_seconds=timeout_seconds,
                )
        elif role == "security-agent" and not security_llm_required:
            result = run_deterministic_security(
                project_profile=project_profile,
                repository=worktree,
                artifacts_dir=run_artifacts,
                timeout_seconds=timeout_seconds,
                required_checks=security_required_checks(state),
            )
        elif role == "reviewer" and not reviewer_requires_llm(state, run_artifacts):
            result = run_deterministic_review(
                project_profile=project_profile,
                repository=worktree,
                artifacts_dir=run_artifacts,
            )
        elif role == "orchestrator":
            result = run_deterministic_orchestrator(
                goal=goal,
                project_profile=project_profile,
                repository=worktree,
                artifacts_dir=run_artifacts,
            )
        else:
            capability_error = missing_image_capability(role_goal, run_artifacts) if role == "implementation-agent" else ""
            if capability_error:
                result = awaiting_approval_result("Required implementation capability is unavailable.", [capability_error])
            else:
                contract = role_contract(role)
                manifest_path = create_context_manifest(
                    run_id=run_id,
                    role=role,
                    goal=role_goal,
                    repository=worktree,
                    artifacts_dir=run_artifacts,
                    context_dir=context_dir,
                    project=project,
                    project_profile=project_profile,
                    token_budget=role_token_budget,
                    allowed_tools=role_tools(role),
                    previous_roles=completed_roles,
                    filesystem_access=role_filesystem_access(role),
                    prompt_path=str(contract.get("prompt_path", "")),
                    output_contract=str(contract.get("output_contract", "")),
                    expected_artifacts=list(contract.get("expected_artifacts", [])),
                )
                manifest_errors = validate_manifest(manifest_path, role)
                if manifest_errors:
                    result = blocked_result("Context manifest failed schema validation.", manifest_errors)
                else:
                    preflight_result = preflight_role_execution(
                        role=role,
                        repository=worktree,
                        artifacts_dir=run_artifacts,
                        project_profile=project_profile,
                        dry_run=dry_run,
                    )
                    if preflight_result is not None:
                        result = preflight_result
                    else:
                        risk_path = run_artifacts / "risk.json"
                        risk_artifact = load_json(risk_path) if risk_path.is_file() else {}
                        risk_class = str(
                            risk_artifact.get("risk_class", state.get("risk_class", "low"))
                            if isinstance(risk_artifact, dict)
                            else state.get("risk_class", "low")
                        )
                        context_manifest_value = load_json(manifest_path)
                        context_budget_value = (
                            context_manifest_value.get("context_budget", {})
                            if isinstance(context_manifest_value, dict)
                            else {}
                        )
                        execution_plan_value = (
                            load_json(Path(str(state.get("execution_plan_path", ""))))
                            if effective_mode == "adaptive"
                            and Path(str(state.get("execution_plan_path", ""))).is_file()
                            else {}
                        )
                        planned_profiles = (
                            execution_plan_value.get("model_profiles", {})
                            if isinstance(execution_plan_value, dict)
                            else {}
                        )
                        plan_analysis = (
                            execution_plan_value.get("analysis", {})
                            if isinstance(execution_plan_value, dict)
                            else {}
                        )
                        plan_budgets = (
                            execution_plan_value.get("budgets", {})
                            if isinstance(execution_plan_value, dict)
                            else {}
                        )
                        model_escalation_approval_id = active_model_escalation_approval_id(
                            state, role
                        )
                        bounded_escalation_exhausted = bool(
                            model_escalation_approval_id
                        ) or bounded_model_escalation_checkpoint(state, role)
                        execution_settings = select_execution_profile(
                            role=role,
                            goal=role_goal,
                            risk_class=risk_class,
                            changed_files=workflow_changed_files(state, run_artifacts),
                            changed_lines=changed_line_count(worktree),
                            changed_areas=workflow_changed_areas(state, run_artifacts),
                            repair_iteration=active_repair_iteration(state, role),
                            prior_failure=prior_role_failed(state, role),
                            previous_profile=previous_execution_profile(state, role),
                            previous_reasoning_effort=previous_reasoning_effort(state, role),
                            planned_profile=(
                                str(planned_profiles.get(role, ""))
                                if isinstance(planned_profiles, dict)
                                else ""
                            ),
                            task_complexity=(
                                str(plan_analysis.get("scope", ""))
                                if isinstance(plan_analysis, dict)
                                else ""
                            ),
                            failure_type=model_failure_type(state),
                            context_size=int(context_budget_value.get("used_tokens", 0) or 0),
                            eval_success_rate=state.get("adaptive_eval_success_rate"),
                            required_capability=(
                                "architecture"
                                if role == "architecture-consistency-agent"
                                else "deep_review"
                                if role == "semantic-conflict-agent"
                                else ""
                            ),
                            budget_pressure=(
                                isinstance(state.get("budget_action"), dict)
                                and state["budget_action"].get("action") == "economy"
                            ),
                            repair_count=active_repair_iteration(state, role),
                            max_escalations=(
                                int(plan_budgets.get("max_model_escalations", 2) or 2)
                                if isinstance(plan_budgets, dict)
                                else 2
                            ),
                            human_escalation_approved=bool(model_escalation_approval_id),
                            bounded_escalation_exhausted=bounded_escalation_exhausted,
                        )
                        if (
                            model_escalation_approval_id
                            and execution_settings.get("terminal_action")
                            != "human_or_dead_letter"
                        ):
                            if not consume_model_escalation_approval(
                                run_dir,
                                state,
                                role=role,
                                approval_id=model_escalation_approval_id,
                            ):
                                append_trace(
                                    layout,
                                    {
                                        "event": "runtime.model_escalation_already_consumed",
                                        "role": role,
                                        "approval_id": model_escalation_approval_id,
                                    },
                                )
                                return state
                        state["current_execution_profile"] = execution_settings
                        write_json(layout.workflow, state)
                        append_trace(
                            layout,
                            {
                                "event": "runtime.profile_selected",
                                "role": role,
                                **execution_settings,
                            },
                        )
                        if execution_settings.get("terminal_action") == "human_or_dead_letter":
                            result = awaiting_approval_result(
                                "Bounded model escalation is exhausted.",
                                ["Human review is required before retrying or dead-lettering this role."],
                            )
                        else:
                            request = build_role_request(
                                run_id=run_id,
                                role=role,
                                goal=role_goal,
                                repository=worktree,
                                artifacts_dir=run_artifacts,
                                context_manifest=manifest_path,
                                token_budget=role_token_budget,
                                timeout_seconds=min(timeout_seconds, 180) if effective_mode == "fast" else timeout_seconds,
                                project_profile=project_profile,
                                execution_settings=execution_settings,
                            )
                            write_json(requests_dir / f"{role}.json", request)
                            runtime_invoked = True
                            result = execute_runtime_observed(
                                runtime,
                                run_dir=run_dir,
                                role=role,
                                context=manifest_path,
                                task=request,
                                worktree=worktree,
                                artifacts=run_artifacts,
                            )

        artifact_limit = load_recovery_policy().runtime_limits.max_artifact_bytes
        used_artifact_bytes = artifact_bytes(run_artifacts, stop_after=artifact_limit)
        if used_artifact_bytes > artifact_limit:
            result = blocked_result(
                "Run artifacts exceeded the configured byte limit.",
                [f"artifact bytes {used_artifact_bytes} exceed limit {artifact_limit}"],
            )
            result["_failure"] = {
                "kind": "unrecoverable",
                "error_type": "ArtifactLimitExceeded",
                "message": "Run artifacts exceeded the configured byte limit.",
            }

        if not used_cached_result:
            role_checkpoint(
                run_dir=run_dir,
                run_id=run_id,
                role=role,
                state_name="role_output_received",
                attempt=role_visits[role],
                worktree=worktree,
                input_fingerprint=fingerprint,
                result=result,
            )
            if not isinstance(result.get("_failure"), dict):
                write_json(
                    layout.role_results / f"{role}-pending.json",
                    {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "role": role,
                        "state": "role_output_received",
                        "result": result,
                        "runtime_invoked": runtime_invoked,
                        "execution_profile": execution_settings,
                    },
                )
            role_checkpoint(
                run_dir=run_dir,
                run_id=run_id,
                role=role,
                state_name="role_validating",
                attempt=role_visits[role],
                worktree=worktree,
                input_fingerprint=fingerprint,
                result=result,
            )
        result_errors = validate_role_result(result, role)
        if result_errors:
            result = blocked_result("Role result failed schema validation.", result_errors)
            result["_failure"] = {
                "kind": "invalid_output",
                "error_type": "InvalidStructuredOutput",
                "message": "Role result failed schema validation.",
            }
        deterministic_created = ensure_deterministic_role_artifacts(role, run_artifacts, project_profile)
        if deterministic_created and result.get("status") == "completed":
            created = list(result.get("artifacts_created", []))
            for artifact in deterministic_created:
                if artifact not in created:
                    created.append(artifact)
            result["artifacts_created"] = created
        result.setdefault("duration_ms", int((time.monotonic() - role_started) * 1000))
        contract = role_contract(role)
        expected_artifacts = [
            str(item)
            for item in contract.get("expected_artifacts", [])
            if isinstance(item, str)
        ]
        for parallel_role in parallel_role_results:
            parallel_contract = role_contract(parallel_role)
            expected_artifacts.extend(
                str(item)
                for item in parallel_contract.get("expected_artifacts", [])
                if isinstance(item, str)
            )
        if role == "context-compiler" and effective_mode in {"fast", "adaptive"}:
            expected_artifacts.extend(["plan.md", "risk.json", "project_profile.json"])
        owned_patterns = [
            str(item)
            for item in contract.get("owned_artifact_patterns", [])
            if isinstance(item, str)
        ]
        artifact_ownership_errors = ownership_errors(
            role=role,
            allowed_artifacts=[*expected_artifacts, *owned_patterns],
            before=artifact_snapshot_before,
            after=file_snapshot(run_artifacts),
        )
        if artifact_ownership_errors:
            restore_foreign_artifacts(
                directory=run_artifacts,
                allowed_artifacts=[*expected_artifacts, *owned_patterns],
                before=artifact_contents_before,
            )
            result = blocked_result("Role artifact ownership validation failed.", artifact_ownership_errors)
        if role_filesystem_access(role) != "task_worktree_write" and git_snapshot(worktree) != role_repo_snapshot_before:
            result = blocked_result(
                "Read-only role changed repository contents.",
                [f"{role} changed task worktree code despite {role_filesystem_access(role)} access"],
            )
        if result.get("status") == "completed" and not budget_skipped:
            artifact_errors = validate_role_artifacts(
                role=role,
                result=result,
                artifacts_dir=run_artifacts,
                worktree=worktree,
                source_repository=repository,
                source_snapshot_before=source_snapshot_before,
                create_task_worktree=create_task_worktree,
            )
            if artifact_errors:
                result = blocked_result("Role artifact validation failed.", artifact_errors)
                result["_failure"] = {
                    "kind": "invalid_output",
                    "error_type": "InvalidArtifactOutput",
                    "message": "Role artifact validation failed.",
                }
        checkpoint = {
            "time": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "execution_kind": planned_execution_kind(state, role),
            "llm_invoked": runtime_invoked,
            "cached_result": used_cached_result,
            "cached_result_replay": cached_result_replay,
            "cache_provenance": (
                "completed_checkpoint_replay"
                if cached_result_replay
                else "pending_output"
                if used_cached_result
                else ""
            ),
            "prompt_file": PROMPT_FILES.get(role, ""),
            "prompt": role_prompt(role),
            "result": result,
        }
        if execution_settings:
            checkpoint["execution_profile"] = execution_settings
        state["roles"].append(checkpoint)
        if result.get("status") == "completed":
            completed_roles.append(role)
        write_json(layout.role_results / f"{role}-{role_visits[role]}.json", checkpoint)
        append_trace(layout, {"event": "role_completed", "role": role, "result": result})
        for parallel_role, parallel_result in parallel_role_results.items():
            parallel_errors = validate_role_result(parallel_result, parallel_role)
            if (
                not parallel_errors
                and parallel_result.get("status") == "completed"
                and parallel_result.get("budget_skipped") is not True
            ):
                parallel_errors = validate_role_artifacts(
                    role=parallel_role,
                    result=parallel_result,
                    artifacts_dir=run_artifacts,
                    worktree=worktree,
                    source_repository=repository,
                    source_snapshot_before=source_snapshot_before,
                    create_task_worktree=create_task_worktree,
                )
            if parallel_errors:
                parallel_result = blocked_result(
                    f"Parallel role {parallel_role} failed validation.",
                    parallel_errors,
                )
            parallel_result.setdefault("duration_ms", int((time.monotonic() - role_started) * 1000))
            role_visits[parallel_role] = role_visits.get(parallel_role, 0) + 1
            parallel_checkpoint = {
                "time": datetime.now(timezone.utc).isoformat(),
                "role": parallel_role,
                "execution_kind": planned_execution_kind(state, parallel_role),
                "llm_invoked": execution_settings_invoke_runtime(
                    parallel_role_settings.get(parallel_role, {})
                ),
                "prompt_file": PROMPT_FILES.get(parallel_role, ""),
                "prompt": role_prompt(parallel_role),
                "parallel_group": [role, parallel_role],
                "result": parallel_result,
            }
            if parallel_role_settings.get(parallel_role):
                parallel_checkpoint["execution_profile"] = parallel_role_settings[parallel_role]
            state["roles"].append(parallel_checkpoint)
            if parallel_result.get("status") == "completed":
                completed_roles.append(parallel_role)
            write_json(
                layout.role_results / f"{parallel_role}-{role_visits[parallel_role]}.json",
                parallel_checkpoint,
            )
            append_trace(
                layout,
                {
                    "event": "parallel_role_completed",
                    "role": parallel_role,
                    "parallel_with": role,
                    "result": parallel_result,
                },
            )
        if isinstance(result.get("_failure"), dict):
            pending_path = layout.role_results / f"{role}-pending.json"
            if pending_path.exists():
                pending_path.unlink()
            role_checkpoint(
                run_dir=run_dir,
                run_id=run_id,
                role=role,
                state_name="role_running",
                attempt=role_visits[role],
                worktree=worktree,
                input_fingerprint=fingerprint,
            )
            recovery_event = schedule_role_recovery(
                layout=layout,
                state=state,
                role=role,
                result=result,
            )
            state["role_count"] = accounted_role_count(state["roles"])
            state["tokens_used"] = accounted_tokens_used(state["roles"])
            state["elapsed_seconds"] = elapsed_before_resume + int(time.monotonic() - workflow_started)
            write_json(layout.workflow, state)
            write_metrics(layout, state)
            append_trace(layout, {"event": "role_recovery_scheduled", "role": role, **recovery_event})
            if state["execution_status"] == "awaiting_approval":
                attention_action = role_attention_action(result)
                is_question = attention_action == "answer"
                repeated_question = set_attention(
                    state,
                    summary=str(result.get("summary", state.get("recovery_reason", "User input is required."))),
                    details=[str(item) for item in result.get("blockers", [])],
                    role=role,
                    action=attention_action,
                    question=result.get("question"),
                    stop_if_previously_answered=is_question,
                )
                if repeated_question:
                    state["execution_status"] = "blocked"
                    persist_control_failure(
                        layout, state, role=role, stage="role", kind="internal_error",
                        error_type="RepeatedMissingRequirement", message=str(state["attention"]["summary"]),
                    )
                    append_trace(
                        layout,
                        {"event": "repeated_question_stopped", "role": role},
                    )
                else:
                    try:
                        request_approval(
                            layout.root,
                            reason=str(state["attention"]["summary"]),
                        )
                    except ApprovalError as exc:
                        state["execution_status"] = "blocked"
                        set_attention(
                            state,
                            summary="The approval request could not be created.",
                            details=[f"approval request failed: {exc}"],
                            role="approval-gate",
                            action="fix_then_retry",
                        )
                        persist_control_failure(
                            layout, state, role="approval-gate", stage="approval",
                            kind="internal_error", error_type="ApprovalRequestFailed", message=str(exc),
                        )
                        write_json(layout.workflow, state)
            state["base_branch_sha_after"] = git_ref_sha(repository, effective_base)
            write_json(layout.workflow, state)
            write_metrics(layout, state)
            return state
        if result.get("status") != "completed":
            record_failure(
                layout,
                stage="role",
                role=role,
                code="ROLE_NOT_COMPLETED",
                message=str(result.get("summary", "Role did not complete.")),
                details=[str(item) for item in result.get("blockers", [])],
            )
            persist_control_failure(
                layout, state, role=role, stage="role", kind="human_input_required",
                error_type="RoleNotCompleted", message=str(result.get("summary", "Role did not complete.")),
            )
        else:
            role_checkpoint(
                run_dir=run_dir,
                run_id=run_id,
                role=role,
                state_name="role_completed",
                attempt=role_visits[role],
                worktree=worktree,
                input_fingerprint=fingerprint,
                result=result,
            )
            pending_path = layout.role_results / f"{role}-pending.json"
            if pending_path.exists():
                pending_path.unlink()

        state["role_count"] = accounted_role_count(state["roles"])
        state["tokens_used"] = accounted_tokens_used(state["roles"])
        state["elapsed_seconds"] = elapsed_before_resume + int(time.monotonic() - workflow_started)
        route = decide_next_role(
            current_role=role,
            role_result=result,
            run_dir=run_dir,
            artifacts_dir=run_artifacts,
            workflow_state=state,
        )
        route_errors = validate_contract(
            route,
            load_json(SCHEMAS / "workflow_route.schema.json"),
            "workflow_route",
        )
        if route_errors:
            route = {
                "next_role": "approval-gate",
                "reason": "Deterministic router returned an invalid route contract.",
                "stop": True,
                "publication_allowed": False,
                "loop": None,
                "warnings": route_errors,
            }
            record_failure(
                layout,
                stage="routing",
                code="INVALID_ROUTE_CONTRACT",
                message=route["reason"],
                details=route_errors,
            )
            persist_control_failure(
                layout, state, role=role, stage="routing", kind="internal_error",
                error_type="InvalidRouteContract", message="; ".join(route_errors),
            )
        spawned_children: list[dict[str, Any]] = []
        child_proposals = result.get("child_tasks")
        if result.get("status") == "completed" and child_proposals:
            try:
                spawned_children = spawn_children(
                    queue=TaskQueue(DEFAULT_DB),
                    state=state,
                    role=role,
                    proposals=child_proposals,
                )
            except (TaskGraphError, OSError, ValueError) as exc:
                state["execution_status"] = "blocked"
                set_attention(
                    state,
                    summary="A proposed background task violated the task-graph contract.",
                    details=[str(exc)],
                    role=role,
                    action="fix_then_retry",
                )
                persist_control_failure(
                    layout,
                    state,
                    role=role,
                    stage="child_spawn",
                    kind="policy_block",
                    error_type="InvalidChildTaskProposal",
                    message=str(exc),
                )
                write_json(layout.workflow, state)
                write_metrics(layout, state)
                return state
        if state.get("parent_run_id") and route.get("next_role") in {
            "publication-prepare",
            "publication",
        }:
            route = {
                "next_role": "",
                "reason": "Child verification completed; only the root run may publish.",
                "stop": True,
                "publication_allowed": False,
                "loop": None,
                "warnings": [],
            }
        if route.get("next_role") == "publication-prepare" and state.get("children"):
            state["wait_for_children"] = [
                str(child.get("run_id", ""))
                for child in state["children"]
                if isinstance(child, dict) and child.get("join_status") != "joined"
            ]
            ready, join_problems = join_parent_children(
                queue=TaskQueue(DEFAULT_DB),
                state=state,
                runs_dir=RUNS,
            )
            if join_problems:
                route = {
                    "next_role": "implementation-agent",
                    "reason": "Background child results require parent integration repair.",
                    "stop": False,
                    "publication_allowed": False,
                    "loop": None,
                    "warnings": join_problems,
                }
            elif not ready:
                state["resume_after_children"] = "publication-prepare"
                state["execution_status"] = "waiting_children"
                state["last_route"] = route
                write_json(layout.workflow, state)
                write_metrics(layout, state)
                append_trace(
                    layout,
                    {"event": "workflow_waiting_for_children", "children": state["wait_for_children"]},
                )
                return state
        state["last_route"] = route
        write_json(layout.workflow, state)
        write_metrics(layout, state)
        append_trace(
            layout,
            {
                "type": "router.decision",
                "event": "router.decision",
                "run_id": run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "current_role": role,
                "next_role": route["next_role"],
                "reason": route["reason"],
                "loop": route.get("loop"),
                "publication_allowed": route["publication_allowed"],
                "warnings": route.get("warnings", []),
            },
        )
        blocking_children = [
            str(child.get("run_id", ""))
            for child in spawned_children
            if child.get("dependency_mode") == "blocking"
        ]
        if blocking_children and not route["stop"]:
            state["wait_for_children"] = blocking_children
            state["resume_after_children"] = str(route["next_role"])
            state["execution_status"] = "waiting_children"
            write_json(layout.workflow, state)
            write_metrics(layout, state)
            append_trace(
                layout,
                {"event": "workflow_waiting_for_children", "children": blocking_children},
            )
            return state
        if route["stop"]:
            if route["next_role"] == "approval-gate":
                state["execution_status"] = "awaiting_approval"
                approval = awaiting_approval_result(route["reason"], route.get("warnings", []))
                attention_details = [
                    *[str(item) for item in result.get("blockers", [])],
                    *[str(item) for item in route.get("warnings", [])],
                ]
                attention_summary = (
                    str(result.get("summary", "")).strip()
                    if result.get("status") in {"blocked", "failed", "awaiting_approval"}
                    else ""
                ) or route["reason"]
                attention_action = role_attention_action(result)
                is_question = attention_action == "answer"
                repeated_question = set_attention(
                    state,
                    summary=attention_summary,
                    details=attention_details,
                    role=role,
                    action=attention_action,
                    question=result.get("question"),
                    stop_if_previously_answered=is_question,
                )
                if repeated_question:
                    state["execution_status"] = "blocked"
                    record_failure(
                        layout,
                        stage="routing",
                        code="REPEATED_REQUIREMENT",
                        message=str(state["attention"]["summary"]),
                        details=list(state["attention"]["details"]),
                    )
                    persist_control_failure(
                        layout, state, role=role, stage="routing", kind="internal_error",
                        error_type="RepeatedMissingRequirement", message=str(state["attention"]["summary"]),
                    )
                    append_trace(layout, {"event": "repeated_question_stopped", "role": role})
                else:
                    approval_checkpoint = {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "role": "approval-gate",
                        "execution_kind": "harness_stage",
                        "llm_invoked": False,
                        "prompt_file": "",
                        "prompt": "",
                        "result": approval,
                    }
                    state["roles"].append(approval_checkpoint)
                    state["role_count"] = accounted_role_count(state["roles"])
                    write_json(layout.role_results / "approval-gate-1.json", approval_checkpoint)
                    write_json(layout.workflow, state)
                    try:
                        request_approval(layout.root, reason=attention_summary)
                    except ApprovalError as exc:
                        state["execution_status"] = "blocked"
                        set_attention(
                            state,
                            summary="The approval request could not be created.",
                            details=[f"approval request failed: {exc}"],
                            role="approval-gate",
                            action="fix_then_retry",
                        )
                        record_failure(
                            layout,
                            stage="approval",
                            code="APPROVAL_REQUEST_FAILED",
                            message="Could not create scoped approval request.",
                            details=[str(exc)],
                        )
                        persist_control_failure(
                            layout, state, role="approval-gate", stage="approval",
                            kind="internal_error", error_type="ApprovalRequestFailed", message=str(exc),
                        )
                    append_trace(layout, {"event": "workflow_awaiting_approval", "reason": route["reason"]})
                    record_failure(
                        layout,
                        stage="routing",
                        code="AWAITING_APPROVAL",
                        message=route["reason"],
                        details=route.get("warnings", []),
                    )
            elif route["next_role"] == "":
                state["execution_status"] = "completed"
            else:
                state["execution_status"] = "blocked"
                set_attention(
                    state,
                    summary=route["reason"],
                    details=[route["reason"], *route.get("warnings", [])],
                    role=role,
                    action="fix_then_retry",
                )
                append_trace(layout, {"event": "workflow_blocked", "reason": route["reason"]})
                record_failure(
                    layout,
                    stage="routing",
                    code="ROUTER_BLOCKED",
                    message=route["reason"],
                    details=route.get("warnings", []),
                )
                persist_control_failure(
                    layout, state, role=role, stage="routing", kind="human_input_required",
                    error_type="RouterBlocked", message=route["reason"],
                )
            break
        effective_mode = str(state.get("effective_mode", effective_mode))
        role = route["next_role"]

    if state["execution_status"] == "running":
        state["execution_status"] = "completed"
    state["base_branch_sha_after"] = git_ref_sha(repository, effective_base)
    write_json(layout.workflow, state)
    write_metrics(layout, state)
    append_trace(layout, {"event": "workflow_finished", "execution_status": state["execution_status"]})
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default="full_agent_workflow")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--task-id", default="task")
    parser.add_argument("--goal", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--branch", default="")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--runtime-provider", default="")
    parser.add_argument("--runtime-command", default="")
    parser.add_argument("--adapter-command", default="", help=argparse.SUPPRESS)
    parser.add_argument("--token-budget", type=int, default=12000)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--worktree", action="store_true")
    parser.add_argument("--create-worktree", dest="worktree", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--current-branch", action="store_true")
    parser.add_argument("--mode", choices=sorted(EXECUTION_MODES), default="auto")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = run_roles(
        workflow=args.workflow,
        run_id=args.run_id,
        artifacts_dir=args.artifacts_dir,
        dry_run=args.dry_run,
        task_id=args.task_id,
        goal=args.goal,
        project=args.project,
        repository=args.repo,
        branch=args.branch,
        base_branch=args.base_branch,
        adapter_command=args.adapter_command,
        token_budget=args.token_budget,
        timeout_seconds=args.timeout_seconds,
        create_task_worktree=args.worktree,
        current_branch=args.current_branch,
        mode=args.mode,
        resume=args.resume,
        runtime_provider=args.runtime_provider,
        runtime_command=args.runtime_command,
    )
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0 if state["execution_status"] in {"completed", "waiting_children"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
