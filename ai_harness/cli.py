"""User-facing `agent` command for local project onboarding and daily tasks."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .paths import HarnessNotFoundError, harness_home
from .project import (
    ALLOWED_BRANCH_PREFIXES,
    SUPPORTED_PROFILES,
    ProjectConfigError,
    default_config,
    discover_repository,
    load_project_config,
    project_is_trusted,
    register_local_project,
    safe_branch,
    slug,
    write_project_config,
)


AGENTS_TEMPLATE = """# AGENTS.md

## Project

This repository is initialized for the local AI Harness. Project metadata lives in `.agent/project.yaml`.

## Working rules

- Make minimal, reviewable changes in the task worktree.
- Never commit directly to the default branch.
- Run the project checks selected by the Harness before review or publication.
- Do not expose secrets, private data, raw traces, or local Harness state.
- Never auto-merge or deploy without explicit human approval.
- Follow any more specific repository instructions added below this section.
"""


class CLIError(RuntimeError):
    """A concise user-facing command error."""


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


def emit(payload: dict[str, Any], *, as_json: bool, lines: Sequence[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for line in lines:
        print(line)


def repository_from_arg(value: str, *, require_initialized: bool) -> Path:
    explicit = bool(value)
    repository = discover_repository(Path(value or Path.cwd()), explicit=explicit)
    if require_initialized:
        load_project_config(repository)
    return repository


def install_harness_import_path(root: Path) -> None:
    scripts = str((root / "scripts").resolve())
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def load_harness_module(root: Path, name: str) -> Any:
    install_harness_import_path(root)
    return importlib.import_module(name)


def handle_init(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=False)
    config = default_config(
        repository,
        project_id=args.project_id,
        profile=args.profile,
        base_branch=args.base_branch,
        branch_prefix=args.branch_prefix,
    )
    config_created = write_project_config(config, force=args.force)
    trust_path = register_local_project(config)
    agents_path = repository / "AGENTS.md"
    agents_created = False
    if agents_path.is_symlink():
        raise CLIError("refusing to write AGENTS.md through a symbolic link")
    if args.force or not agents_path.exists():
        agents_path.write_text(AGENTS_TEMPLATE, encoding="utf-8")
        agents_created = True
    payload = {
        "status": "initialized",
        "repository": str(repository),
        "project_config": str(config.path),
        "project_id": config.project_id,
        "profile": config.profile,
        "runtime_provider": config.runtime_provider,
        "local_trust": str(trust_path),
        "created": {
            "project_config": config_created,
            "agents_md": agents_created,
        },
    }
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"Initialized {config.project_id} ({config.profile})",
            f"  config: {config.path}",
            f"  instructions: {agents_path}{' (kept existing)' if not agents_created else ''}",
            "Next: agent task \"Describe the change\"",
        ),
    )
    return 0


def generated_task_id(goal: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:8]
    return f"{timestamp}-{slug(goal, 'task')[:32]}-{digest}"


def handle_task(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise CLIError("project configuration is not locally trusted; run `agent init` again")
    root = harness_home()
    event_ingestion = load_harness_module(root, "event_ingestion")
    task_queue = load_harness_module(root, "task_queue")
    goal = " ".join(args.goal).strip()
    if not goal:
        raise CLIError("task goal is required")
    if len(goal) > 20_000:
        raise CLIError("task goal must not exceed 20000 characters")
    task_id = slug(args.task_id, "") if args.task_id else generated_task_id(goal)
    if not task_id:
        raise CLIError("--task-id must contain at least one letter or number")
    branch = args.branch or f"{config.branch_prefix}{slug(task_id, 'task')}"
    if not safe_branch(branch):
        raise CLIError("task branch must be a safe git branch name")
    if branch in {config.base_branch, "main", "master", "trunk"}:
        raise CLIError("task branch must not be a protected/default branch")
    if not any(branch.startswith(prefix) for prefix in ALLOWED_BRANCH_PREFIXES):
        raise CLIError(f"task branch must use one of {sorted(ALLOWED_BRANCH_PREFIXES)}")
    external_id = f"{config.project_id}:{task_id}"
    payload = {
        "external_id": external_id,
        "task_id": task_id,
        "task_key": f"cli:{config.project_id}:{task_id}",
        "goal": goal,
        "branch": branch,
        "base_branch": config.base_branch,
        "priority": args.priority,
        "max_retries": args.max_retries,
    }
    envelope = event_ingestion.normalize_event(
        source="cli",
        payload=payload,
        repository=repository,
        project=config.profile,
    )
    if args.dry_run:
        emit(
            {"status": "dry_run", "envelope": envelope},
            as_json=args.json,
            lines=(
                f"Dry run: {task_id}",
                f"  repository: {repository}",
                f"  branch: {branch}",
                "No queue state was changed.",
            ),
        )
        return 0
    queue_path = root / ".agent-queue" / "tasks.db"
    record = event_ingestion.enqueue_envelope(task_queue.TaskQueue(queue_path), envelope)
    stored_branch = str(record.payload.get("branch", branch))
    result = {
        "status": record.status,
        "queue_task_id": record.id,
        "task_id": task_id,
        "task_key": record.task_key,
        "repository": str(repository),
        "branch": stored_branch,
        "queue_db": str(queue_path),
        "idempotent": record.payload.get("event_id") == envelope["event_id"],
    }
    emit(
        result,
        as_json=args.json,
        lines=(
            f"Task queued: {task_id}",
            f"  queue id: {record.id}",
            f"  branch: {stored_branch}",
            f"  status: {record.status}",
            "Inspect it with: agent status",
        ),
    )
    return 0


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def worker_service_status(root: Path) -> dict[str, Any]:
    state = read_json_object(root / ".agent-queue" / "worker-service.json")
    pid = int(state.get("pid", 0) or 0)
    return {
        "configured": bool(state),
        "alive": process_alive(pid),
        "pid": pid,
        "service_id": str(state.get("service_id", "")),
        "status": str(state.get("status", "not_started")),
    }


def project_tasks(db_path: Path, repository: Path) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=2) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 500").fetchall()
    except sqlite3.Error as exc:
        raise CLIError(f"cannot read queue database: {exc}") from exc
    selected: list[dict[str, Any]] = []
    for row in rows:
        try:
            task_payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(task_payload, dict):
            continue
        repository_value = str(task_payload.get("repository", ""))
        if not repository_value:
            continue
        task_repository = Path(repository_value).expanduser()
        if task_repository.resolve() != repository.resolve():
            continue
        selected.append(
            {
                "queue_task_id": int(row["id"]),
                "task_id": str(task_payload.get("task_id", "")),
                "goal": str(task_payload.get("goal", "")),
                "status": str(row["status"]),
                "run_id": str(row["run_id"]),
                "requires_human": bool(row["requires_human"]),
                "exception_reason": str(row["exception_reason"]),
                "failure_kind": str(row["failure_kind"]) if "failure_kind" in row.keys() else "",
                "recovery_action": str(row["recovery_action"]) if "recovery_action" in row.keys() else "",
                "next_attempt_at": float(row["next_attempt_at"]) if "next_attempt_at" in row.keys() else 0.0,
                "resume_checkpoint": str(row["resume_checkpoint"]) if "resume_checkpoint" in row.keys() else "",
                "recovery_attempts": int(row["recovery_attempts"]) if "recovery_attempts" in row.keys() else 0,
                "last_failure_id": str(row["last_failure_id"]) if "last_failure_id" in row.keys() else "",
                "updated_at": float(row["updated_at"]),
            }
        )
    return selected


def project_runs(runs_dir: Path, repository: Path) -> list[dict[str, Any]]:
    if not runs_dir.is_dir():
        return []
    selected: list[dict[str, Any]] = []
    for run_dir in sorted(runs_dir.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not run_dir.is_dir():
            continue
        workflow = read_json_object(run_dir / "workflow.json")
        if not workflow:
            continue
        repository_value = str(workflow.get("repository", ""))
        if not repository_value:
            continue
        source_repository = Path(repository_value).expanduser()
        if source_repository.resolve() != repository.resolve():
            continue
        approval = read_json_object(run_dir / "artifacts" / "approval.json")
        verdict = read_json_object(run_dir / "artifacts" / "verdict.json")
        current_failure = read_json_object(run_dir / "failures" / f"{workflow.get('failure_id', '')}.json")
        verdict_blockers = verdict.get("blockers", [])
        approval_detail = str(approval.get("reason", ""))
        if isinstance(verdict_blockers, list) and verdict_blockers:
            approval_detail = str(verdict_blockers[0])
        registration_required = (
            str(approval.get("checkpoint_role", "")) == "orchestrator"
            and verdict.get("decision") == "await_approval"
            and "not registered" in approval_detail.lower()
        )
        selected.append(
            {
                "run_id": run_dir.name,
                "task_id": str(workflow.get("task_id", "")),
                "status": str(workflow.get("execution_status", "unknown")),
                "role_count": int(workflow.get("role_count", 0) or 0),
                "tokens_used": int(workflow.get("tokens_used", 0) or 0),
                "branch": str(workflow.get("branch", "")),
                "blockers": [str(item) for item in workflow.get("blockers", [])],
                "current_role": str(workflow.get("current_role", "")),
                "failure_id": str(workflow.get("failure_id", "")),
                "failure_kind": str(workflow.get("failure_kind", "")),
                "recovery_action": str(workflow.get("recovery_action", "")),
                "recovery_reason": str(workflow.get("recovery_reason", "")),
                "resume_from": str(workflow.get("resume_from", "")),
                "retry_after_seconds": int(workflow.get("retry_after_seconds", 0) or 0),
                "failure_attempt": int(current_failure.get("attempt", 0) or 0),
                "failure_max_attempts": int(current_failure.get("max_attempts", 0) or 0),
                "failure_error_type": str(current_failure.get("error_type", "")),
                "failure_message": str(current_failure.get("message", "")),
                "approval": {
                    "status": str(approval.get("status", "")),
                    "reason": str(approval.get("reason", "")),
                    "detail": approval_detail,
                    "gate": str(approval.get("checkpoint_role", "")),
                    "registration_required": registration_required,
                },
            }
        )
    return selected


def handle_approve(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise CLIError("project configuration is not locally trusted; run `agent init` again")
    root = harness_home()
    approval_lifecycle = load_harness_module(root, "approval_lifecycle")
    task_queue = load_harness_module(root, "task_queue")
    candidates = []
    for run in project_runs(root / ".agent-runs", repository):
        if run["status"] != "awaiting_approval":
            continue
        if run["approval"]["status"] != "pending":
            continue
        candidates.append(run)
    if args.run_id:
        candidates = [run for run in candidates if run["run_id"] == args.run_id]
        if not candidates:
            raise CLIError(f"pending approval for run {args.run_id!r} was not found")
    elif len(candidates) > 1:
        run_ids = ", ".join(run["run_id"] for run in candidates)
        raise CLIError(f"multiple approvals are pending ({run_ids}); pass --run-id")
    if not candidates:
        raise CLIError("no pending approval was found for this project")
    selected = candidates[0]
    if selected["approval"]["registration_required"]:
        raise CLIError(
            "publication approval cannot bypass the trusted repository registry; "
            "register the target repository before publishing"
        )
    run_dir = root / ".agent-runs" / selected["run_id"]
    reason = args.reason.strip() or "Approved by the user through the agent CLI."
    try:
        approval = approval_lifecycle.approve_run(
            run_dir,
            actor=args.actor,
            reason=reason,
        )
        transition, record = approval_lifecycle.resume_run(
            run_dir,
            queue=task_queue.TaskQueue(root / ".agent-queue" / "tasks.db"),
        )
    except approval_lifecycle.ApprovalError as exc:
        raise CLIError(str(exc)) from exc
    payload = {
        "status": "queued",
        "run_id": selected["run_id"],
        "approval_id": approval["approval_id"],
        "checkpoint_role": approval["checkpoint_role"],
        "queue_task_id": record.id,
        "task_id": record.payload.get("task_id", ""),
        "repository": str(repository),
        "execution_status": transition["workflow"]["execution_status"],
    }
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"Approval accepted: {selected['run_id']}",
            f"  checkpoint: {approval['checkpoint_role']}",
            f"  continuation queue id: {record.id}",
            "Inspect it with: agent status",
        ),
    )
    return 0


def handle_status(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise CLIError("project configuration is not locally trusted; run `agent init` again")
    root = harness_home()
    tasks = project_tasks(root / ".agent-queue" / "tasks.db", repository)
    runs = project_runs(root / ".agent-runs", repository)
    task_counts = dict(Counter(item["status"] for item in tasks))
    run_counts = dict(Counter(item["status"] for item in runs))
    service = worker_service_status(root)
    payload = {
        "project": {
            "id": config.project_id,
            "profile": config.profile,
            "repository": str(repository),
            "runtime_provider": config.runtime_provider,
        },
        "queue": {"counts": task_counts, "items": tasks[: args.limit]},
        "runs": {"counts": run_counts, "items": runs[: args.limit]},
        "worker_service": service,
    }
    lines = [
        f"Project: {config.project_id} ({config.profile})",
        f"Repository: {repository}",
        "Queue: " + (", ".join(f"{key}={value}" for key, value in sorted(task_counts.items())) or "empty"),
        "Runs: " + (", ".join(f"{key}={value}" for key, value in sorted(run_counts.items())) or "none"),
        f"Worker service: {service['status']} ({'running' if service['alive'] else 'not running'})",
    ]
    for item in tasks[: args.limit]:
        marker = " !" if item["requires_human"] else ""
        lines.append(f"  task {item['queue_task_id']}: {item['task_id']} [{item['status']}]{marker}")
        if item["failure_kind"]:
            lines.append(
                f"    failure: {item['failure_kind']}; action: {item['recovery_action'] or 'none'}; "
                f"attempt: {item['recovery_attempts']}; checkpoint: {item['resume_checkpoint'] or 'none'}"
            )
            if item["next_attempt_at"]:
                retry_at = datetime.fromtimestamp(item["next_attempt_at"], timezone.utc).isoformat()
                lines.append(f"    next retry: {retry_at}")
        if item["exception_reason"]:
            lines.append(f"    cause: {item['exception_reason']}")
    for run in runs[: args.limit]:
        if run["status"] in {"retry_wait", "repairing", "resuming", "dead_letter", "failed"}:
            lines.append(
                f"  run {run['run_id']}: {run['status']} role={run['current_role'] or 'unknown'} "
                f"failure={run['failure_kind'] or 'unknown'} action={run['recovery_action'] or 'none'} "
                f"attempt={run['failure_attempt']}/{run['failure_max_attempts']} "
                f"checkpoint={run['resume_from'] or 'none'}"
            )
            if run["failure_error_type"] or run["failure_message"]:
                lines.append(
                    f"    cause: {run['failure_error_type'] or 'unknown'}: "
                    f"{run['failure_message'] or run['recovery_reason']}"
                )
        if run["status"] != "awaiting_approval" or run["approval"]["status"] != "pending":
            continue
        lines.append(
            f"  approval {run['run_id']}: {run['approval']['detail']} "
            f"(gate: {run['approval']['gate']})"
        )
        if run["approval"]["registration_required"]:
            lines.append("    publication requires trusted repository registration")
        else:
            lines.append(f"    agent approve --repo {repository} --run-id {run['run_id']}")
    emit(payload, as_json=args.json, lines=lines)
    return 0


def failure_records(run_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((run_dir / "failures").glob("*.json")) if (run_dir / "failures").is_dir() else []:
        value = read_json_object(path)
        if value:
            records.append(value)
    return records


def handle_failures(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    root = harness_home()
    runs = project_runs(root / ".agent-runs", repository)
    if args.run_id:
        runs = [run for run in runs if run["run_id"] == args.run_id]
    items = [
        failure
        for run in runs
        for failure in failure_records(root / ".agent-runs" / run["run_id"])
    ]
    payload = {"count": len(items), "items": items}
    lines = [f"Failures: {len(items)}"]
    for item in items[-args.limit :]:
        lines.append(
            f"  {item.get('run_id', '')}: {item.get('kind', '')}/{item.get('error_type', '')} "
            f"attempt {item.get('attempt', 0)}/{item.get('max_attempts', 0)} at {item.get('role', '') or item.get('stage', '')}"
        )
    emit(payload, as_json=args.json, lines=lines)
    return 0


def handle_dead_letters(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    root = harness_home()
    items = [item for item in project_tasks(root / ".agent-queue" / "tasks.db", repository) if item["status"] == "dead_letter"]
    emit(
        {"count": len(items), "items": items[: args.limit]},
        as_json=args.json,
        lines=(
            f"Dead letters: {len(items)}",
            *(f"  {item['run_id'] or item['task_id']}: {item['exception_reason'] or item['failure_kind']}" for item in items[: args.limit]),
        ),
    )
    return 0


def handle_recovery_command(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    root = harness_home()
    matching = [run for run in project_runs(root / ".agent-runs", repository) if run["run_id"] == args.run_id]
    if not matching:
        raise CLIError(f"run {args.run_id!r} was not found for this project")
    run_dir = root / ".agent-runs" / args.run_id
    workflow_path = run_dir / "workflow.json"
    workflow = read_json_object(workflow_path)
    if args.command in {"retry", "resume"} and workflow.get("execution_status") == "awaiting_approval":
        raise CLIError("an awaiting_approval run must be continued with `agent approve`")
    task_queue = load_harness_module(root, "task_queue")
    queue = task_queue.TaskQueue(root / ".agent-queue" / "tasks.db")
    try:
        if args.command == "abort":
            record = queue.abort_run(args.run_id)
            workflow["execution_status"] = "cancelled"
            workflow["recovery_action"] = ""
        else:
            record = queue.recover_run(args.run_id, action=args.command)
            workflow["execution_status"] = "retry_wait" if args.command == "retry" else "resuming"
            workflow["recovery_action"] = args.command
            recovery = workflow.get("recovery", {})
            if not isinstance(recovery, dict):
                recovery = {}
            recovery.update(
                {
                    "attempts": 0,
                    "consecutive_failures": 0,
                    "resume_attempts": 0,
                    "elapsed_seconds": 0,
                    "started_at": datetime.now(timezone.utc).timestamp(),
                    "attempts_by_kind": {},
                }
            )
            workflow["recovery"] = recovery
            workflow["manual_recovery_count"] = int(workflow.get("manual_recovery_count", 0) or 0) + 1
            if args.command == "resume" and not workflow.get("resume_role"):
                workflow["resume_role"] = workflow.get("current_role", "")
        temporary = workflow_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(workflow_path)
    except (ValueError, RuntimeError) as exc:
        raise CLIError(str(exc)) from exc
    emit(
        {"status": record.status, "run_id": args.run_id, "queue_task_id": record.id, "action": args.command},
        as_json=args.json,
        lines=(f"Run {args.run_id}: {record.status}", f"  action: {args.command}"),
    )
    return 0


def configured_codex_command() -> str:
    configured = os.environ.get("AGENT_CODEX_CLI_COMMAND", "").strip()
    if configured:
        try:
            parts = shlex.split(configured)
        except ValueError:
            return ""
        executable = parts[0] if parts else ""
        return configured if Path(executable).exists() or shutil.which(executable) else ""
    candidates = (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("codex") or ""


def handle_doctor(args: argparse.Namespace) -> int:
    checks: list[DoctorCheck] = []
    repository: Path | None = None
    try:
        repository = repository_from_arg(args.repo, require_initialized=False)
        checks.append(DoctorCheck("repository", "pass", str(repository)))
    except ProjectConfigError as exc:
        checks.append(DoctorCheck("repository", "fail", str(exc)))
    root: Path | None = None
    try:
        root = harness_home()
        checks.append(DoctorCheck("harness", "pass", str(root)))
    except HarnessNotFoundError as exc:
        checks.append(DoctorCheck("harness", "fail", str(exc)))
    if repository is not None:
        try:
            config = load_project_config(repository)
            checks.append(DoctorCheck("project_config", "pass", str(config.path)))
            trusted = project_is_trusted(config)
            checks.append(
                DoctorCheck(
                    "local_trust",
                    "pass" if trusted else "fail",
                    "configuration fingerprint is trusted"
                    if trusted
                    else "run `agent init` again",
                )
            )
        except ProjectConfigError as exc:
            checks.append(DoctorCheck("project_config", "fail", str(exc)))
        git = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        checks.append(
            DoctorCheck(
                "git_repository",
                "pass" if git.returncode == 0 and git.stdout.strip() == "true" else "warn",
                "git worktree" if git.returncode == 0 else "not a git repository",
            )
        )
    codex = configured_codex_command()
    checks.append(
        DoctorCheck(
            "codex_cli",
            "pass" if codex else "fail",
            codex or "Codex CLI was not found",
        )
    )
    if args.full and root is not None and repository is not None and codex:
        environment = os.environ.copy()
        environment["AGENT_CODEX_CLI_COMMAND"] = codex
        try:
            probe = subprocess.run(
                [sys.executable, str(root / "scripts" / "check_runtime.py"), "--repo", str(repository)],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=90,
            )
            detail = probe.stdout.strip() or probe.stderr.strip() or f"exit {probe.returncode}"
            checks.append(
                DoctorCheck("runtime_preflight", "pass" if probe.returncode == 0 else "fail", detail)
            )
        except subprocess.TimeoutExpired:
            checks.append(DoctorCheck("runtime_preflight", "fail", "runtime preflight timed out after 90s"))
    if root is not None:
        service = worker_service_status(root)
        checks.append(
            DoctorCheck(
                "worker_service",
                "pass" if service["alive"] else "warn",
                f"running pid={service['pid']}" if service["alive"] else "not running; tasks remain queued",
            )
        )
    failed = any(check.status == "fail" for check in checks)
    payload = {
        "status": "fail" if failed else "pass",
        "version": __version__,
        "checks": [asdict(check) for check in checks],
    }
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"AI Harness {__version__}: {'problems found' if failed else 'ready'}",
            *(f"  [{check.status.upper()}] {check.name}: {check.detail}" for check in checks),
        ),
    )
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize a local project")
    init_parser.add_argument("--repo", default="", help="project directory (default: current directory)")
    init_parser.add_argument("--project-id", default="")
    init_parser.add_argument("--profile", choices=["auto", *sorted(SUPPORTED_PROFILES)], default="auto")
    init_parser.add_argument("--base-branch", default="main")
    init_parser.add_argument("--branch-prefix", choices=sorted(ALLOWED_BRANCH_PREFIXES), default="tast/")
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument("--json", action="store_true")
    init_parser.set_defaults(handler=handle_init)

    task_parser = subparsers.add_parser("task", help="enqueue a task for the current project")
    task_parser.add_argument("goal", nargs="+")
    task_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    task_parser.add_argument("--task-id", default="")
    task_parser.add_argument("--branch", default="")
    task_parser.add_argument("--priority", type=int, choices=range(-100, 101), default=0)
    task_parser.add_argument("--max-retries", type=int, choices=range(0, 11), default=2)
    task_parser.add_argument("--dry-run", action="store_true")
    task_parser.add_argument("--json", action="store_true")
    task_parser.set_defaults(handler=handle_task)

    status_parser = subparsers.add_parser("status", help="show compact project queue and run status")
    status_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    status_parser.add_argument("--limit", type=int, choices=range(1, 101), default=10)
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=handle_status)

    failures_parser = subparsers.add_parser("failures", help="list structured task failures")
    failures_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    failures_parser.add_argument("--run-id", default="")
    failures_parser.add_argument("--limit", type=int, choices=range(1, 501), default=50)
    failures_parser.add_argument("--json", action="store_true")
    failures_parser.set_defaults(handler=handle_failures)

    dead_parser = subparsers.add_parser("dead-letters", help="list tasks whose recovery budget is exhausted")
    dead_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    dead_parser.add_argument("--limit", type=int, choices=range(1, 501), default=50)
    dead_parser.add_argument("--json", action="store_true")
    dead_parser.set_defaults(handler=handle_dead_letters)

    for recovery_command in ("retry", "resume", "abort"):
        recovery_parser = subparsers.add_parser(recovery_command, help=f"{recovery_command} an existing run")
        recovery_parser.add_argument("run_id")
        recovery_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
        recovery_parser.add_argument("--json", action="store_true")
        recovery_parser.set_defaults(handler=handle_recovery_command)

    approve_parser = subparsers.add_parser(
        "approve",
        help="approve and resume a pending project run",
    )
    approve_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    approve_parser.add_argument("--run-id", default="", help="specific pending run (optional when only one exists)")
    approve_parser.add_argument("--actor", default="user")
    approve_parser.add_argument("--reason", default="")
    approve_parser.add_argument("--json", action="store_true")
    approve_parser.set_defaults(handler=handle_approve)

    doctor_parser = subparsers.add_parser("doctor", help="diagnose the project and runtime installation")
    doctor_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    doctor_parser.add_argument("--full", action="store_true", help="run the authenticated Runtime preflight")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(handler=handle_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (CLIError, ProjectConfigError, HarnessNotFoundError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
