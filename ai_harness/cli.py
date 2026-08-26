"""User-facing `agent` command for local project onboarding and daily tasks."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.parse
import webbrowser
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml

from . import __version__
from .build import harness_build_fingerprint
from .paths import HarnessNotFoundError, harness_home
from .project import (
    SUPPORTED_PROFILES,
    ProjectConfigError,
    CONFIG_RELATIVE_PATH,
    default_config,
    discover_repository,
    load_project_config,
    project_is_trusted,
    register_local_project,
    safe_branch,
    slug,
    write_project_config,
)
from .recovery.checkpoints import RoleCheckpoint, read_checkpoint, write_checkpoint
from .recovery.models import sanitized_message
from .recovery.policy import load_recovery_policy
from .task_batch import BatchManifestError, parse_batch_manifest


AGENTS_TEMPLATE = """# AGENTS.md

## Project

This repository is initialized for the local AI Harness. Project metadata lives in `.agent/project.yaml`.

## Working rules

- Make minimal, reviewable changes in the task branch.
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


RUNTIME_IMPORTS = (
    "openai_codex",
    "yaml",
    "opentelemetry.trace",
    "opentelemetry.sdk.trace",
)
DEFAULT_UPDATE_SOURCE = "git+https://github.com/mdvcode/agents.git"
REPLACEABLE_CHECKOUT_STATUSES = frozenset({"awaiting_approval", "blocked", "dead_letter", "failed"})


def dependency_repair_hint() -> str:
    return "run `agent update`, then `agent doctor --full`"


def missing_runtime_imports() -> list[str]:
    missing: list[str] = []
    for name in RUNTIME_IMPORTS:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    return missing


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
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        missing = str(getattr(exc, "name", "") or name)
        raise CLIError(
            f"Harness module {name!r} could not load because {missing!r} is unavailable; "
            f"{dependency_repair_hint()}"
        ) from exc


def ignored_setup_files(repository: Path) -> list[str]:
    candidates = [str(CONFIG_RELATIVE_PATH), "AGENTS.md"]
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "--", *candidates],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    ignored = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    return [path for path in candidates if path in ignored]


def handle_init(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=False)
    existing_config = (
        load_project_config(repository)
        if (repository / CONFIG_RELATIVE_PATH).is_file()
        else None
    )
    requested_config = default_config(
        repository,
        project_id=args.project_id or (existing_config.project_id if existing_config else ""),
        profile=(
            existing_config.profile
            if existing_config is not None and args.profile == "auto"
            else args.profile
        ),
        base_branch=(
            existing_config.base_branch
            if existing_config is not None and args.base_branch == "auto"
            else args.base_branch
        ),
        branch_prefix=args.branch_prefix or (existing_config.branch_prefix if existing_config else "feat/"),
    )
    if existing_config is not None and not args.force:
        config = existing_config
        config_created = False
    else:
        config = requested_config
        config_created = write_project_config(config, force=args.force)
    trust_path = register_local_project(config)
    agents_path = repository / "AGENTS.md"
    agents_created = False
    if agents_path.is_symlink():
        raise CLIError("refusing to write AGENTS.md through a symbolic link")
    if args.replace_agents or not agents_path.exists():
        agents_path.write_text(AGENTS_TEMPLATE, encoding="utf-8")
        agents_created = True
    ignored = ignored_setup_files(repository)
    payload = {
        "status": "initialized",
        "repository": str(repository),
        "project_config": str(config.path),
        "project_id": config.project_id,
        "profile": config.profile,
        "base_branch": config.base_branch,
        "runtime_provider": config.runtime_provider,
        "local_trust": str(trust_path),
        "created": {
            "project_config": config_created,
            "agents_md": agents_created,
        },
        "git": {
            "ignored_setup_files": ignored,
        },
    }
    git_guidance = (
        (
            "  local setup: "
            + ", ".join(ignored)
            + " are ignored by Git; this is valid and no git add -f is needed"
        )
        if ignored
        else "  Git note: commit or intentionally ignore new setup files before the first task"
    )
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"Initialized {config.project_id} ({config.profile})",
            f"  config: {config.path}",
            f"  base branch: {config.base_branch}",
            f"  instructions: {agents_path}{' (kept existing)' if not agents_created else ''}",
            git_guidance,
            "Next: agent doctor --full",
        ),
    )
    return 0


def generated_task_id(goal: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:8]
    return f"{timestamp}-{slug(goal, 'task')[:32]}-{digest}"


def generated_task_branch(branch_prefix: str, task_id: str) -> str:
    """Build a deterministic safe branch without exposing prompt quirks to users."""

    task_slug = slug(task_id, "task")
    candidate = f"{branch_prefix}{task_slug}"
    if safe_branch(candidate):
        return candidate
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    return f"task/{digest}"


def supersede_paused_checkout_task(root: Path, queue: Any, conflict: Any, new_task_id: str) -> str:
    """Cancel one paused checkout owner while preserving its branch and run files."""

    run_id = str(conflict.run_id or conflict.payload.get("run_id", "")).strip()
    old_task_id = str(conflict.payload.get("task_id", conflict.id))
    if not run_id:
        raise CLIError(
            f"paused task {old_task_id!r} has no run id and cannot be replaced safely; "
            "inspect it with `agent status`"
        )
    workflow_path = root / ".agent-runs" / run_id / "workflow.json"
    temporary = workflow_path.with_suffix(".json.tmp")
    if workflow_path.is_symlink() or temporary.is_symlink():
        raise CLIError(f"refusing to update paused run {run_id!r} through a symbolic link")
    record = queue.abort_run(run_id)
    if record.status != "cancelled":
        raise CLIError(
            f"task {old_task_id!r} became active while the new task was submitted; "
            "cancellation was requested, and the new task can be retried after `agent status`"
        )
    if workflow_path.is_file():
        workflow = read_json_object(workflow_path)
        workflow["execution_status"] = "cancelled"
        workflow["recovery_action"] = ""
        workflow["superseded_by_task_id"] = new_task_id
        temporary.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(workflow_path)
    return (
        f"paused task {old_task_id} ({run_id}) was replaced by this new task; "
        "its branch and run files were preserved"
    )


def handle_task(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise CLIError("project configuration is not locally trusted; run `agent init` again")
    goal = " ".join(args.goal).strip()
    if not goal:
        raise CLIError("task goal is required")
    if len(goal) > 20_000:
        raise CLIError("task goal must not exceed 20000 characters")
    task_id = slug(args.task_id, "") if args.task_id else generated_task_id(goal)
    if not task_id:
        raise CLIError("--task-id must contain at least one letter or number")
    if args.current_branch and args.branch:
        raise CLIError("--current-branch cannot be combined with --branch")
    if args.current_branch and args.worktree:
        raise CLIError("--current-branch cannot be combined with --worktree")
    workspace_mode = "worktree" if args.worktree else "checkout"
    root = harness_home()
    event_ingestion = load_harness_module(root, "event_ingestion")
    worktree_manager = load_harness_module(root, "worktree_manager") if not args.worktree else None
    intake_base_sha = ""
    if args.current_branch:
        assert worktree_manager is not None
        checkout = worktree_manager.inspect_current_checkout(
            repository,
            protected_branches={config.base_branch, "main", "master", "trunk"},
            require_clean=True,
        )
        checkout_errors = [str(item) for item in checkout.get("errors", [])]
        if checkout_errors:
            raise CLIError("; ".join(checkout_errors))
        branch = str(checkout.get("branch", ""))
        intake_base_sha = str(checkout.get("head_sha", ""))
    else:
        branch = args.branch or generated_task_branch(config.branch_prefix, task_id)
    if not safe_branch(branch):
        source = "current" if args.current_branch else "requested"
        raise CLIError(
            f"{source} branch {branch!r} is not a valid Git branch name; "
            "remove --branch to let agent create one automatically, or switch to a valid existing branch"
        )
    if branch in {config.base_branch, "main", "master", "trunk"}:
        raise CLIError("task branch must not be a protected/default branch")
    external_id = f"{config.project_id}:{task_id}"
    payload = {
        "external_id": external_id,
        "task_id": task_id,
        "task_key": f"cli:{config.project_id}:{task_id}",
        "goal": goal,
        "branch": branch,
        "base_branch": config.base_branch,
        "workspace_mode": workspace_mode,
        "mode": args.mode,
        "priority": args.priority,
        "max_retries": args.max_retries,
        "repository_max_parallel_tasks": int(
            getattr(args, "max_parallel_tasks", 0) or 0
        ),
        "batch_id": str(getattr(args, "batch_id", "")),
        "batch_index": int(getattr(args, "batch_index", 0) or 0),
        "allowed_child_repositories": list(
            getattr(args, "allowed_child_repositories", []) or [str(repository)]
        ),
        "run_id": datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S.%fZ-{task_id}"),
        "checkout_path": str(repository),
        "task_branch": branch,
        "base_sha": intake_base_sha,
        "runtime_provider": config.runtime_provider,
    }
    try:
        envelope = event_ingestion.normalize_event(
            source="cli",
            payload=payload,
            repository=repository,
            project=config.profile,
        )
    except event_ingestion.EventError as exc:
        raise CLIError(f"task request is invalid: {exc}") from exc
    if args.dry_run:
        dry_result = {"status": "dry_run", "envelope": envelope, "task_id": task_id, "repository": str(repository)}
        collector = getattr(args, "result_collector", None)
        if callable(collector):
            collector(dry_result)
            return 0
        emit(
            dry_result,
            as_json=args.json,
            lines=(
                f"Dry run: {task_id}",
                f"  repository: {repository}",
                f"  base branch: {config.base_branch}",
                f"  branch: {branch}",
                f"  workspace: {task_workspace_label(args)}",
                "No queue state was changed.",
            ),
        )
        return 0
    task_queue = load_harness_module(root, "task_queue")
    queue_path = root / ".agent-queue" / "tasks.db"
    try:
        queue = task_queue.TaskQueue(queue_path) if queue_path.is_file() else None
    except (sqlite3.Error, ValueError) as exc:
        raise CLIError(f"task queue could not be opened: {exc}") from exc
    queued_items = queue.list() if queue is not None else []
    existing_same_task = next(
        (item for item in queued_items if item.task_key == envelope["task_key"]),
        None,
    )
    current_branch_conflicts = [
        item
        for item in queued_items
        if item.task_key != envelope["task_key"]
        and item.status not in {"completed", "cancelled"}
        and item.payload.get("workspace_mode") in {"checkout", "current_branch"}
        and bool(item.payload.get("repository"))
        and Path(str(item.payload["repository"])).resolve() == repository.resolve()
    ]
    supersession_warnings: list[str] = []
    if (
        workspace_mode == "checkout"
        and len(current_branch_conflicts) == 1
        and not args.keep_paused
        and current_branch_conflicts[0].status in REPLACEABLE_CHECKOUT_STATUSES
    ):
        supersession_warnings.append(
            supersede_paused_checkout_task(root, queue, current_branch_conflicts[0], task_id)
        )
        current_branch_conflicts = []
    if workspace_mode == "checkout" and current_branch_conflicts:
        conflict = current_branch_conflicts[0]
        raise CLIError(
            f"task {conflict.payload.get('task_id', conflict.id)!r} still owns this checkout "
            f"with status {conflict.status!r}; inspect it with `agent status` "
            "or submit again without --keep-paused to replace a human-paused task"
        )
    missing = missing_runtime_imports()
    if missing:
        raise CLIError(f"missing runtime dependencies: {', '.join(missing)}; {dependency_repair_hint()}")
    branch_warnings: list[str] = list(supersession_warnings)
    prepared: dict[str, object] | None = None
    if not args.current_branch and workspace_mode == "checkout" and existing_same_task is None:
        assert worktree_manager is not None
        prepared = worktree_manager.prepare_task_branch(repository, branch, config.base_branch)
        preparation_errors = [str(item) for item in prepared.get("errors", [])]
        branch_warnings.extend(str(item) for item in prepared.get("warnings", []))
        if preparation_errors:
            rollback_errors = worktree_manager.rollback_prepared_task_branch(repository, prepared)
            if rollback_errors:
                preparation_errors.append(f"branch rollback failed: {'; '.join(rollback_errors)}")
            raise CLIError("; ".join(preparation_errors))
        envelope["base_sha"] = str(prepared.get("base_sha", ""))
        envelope["checkout_path"] = str(repository)
        envelope["task_branch"] = branch
        envelope["branch_owner_run_id"] = str(envelope.get("run_id", ""))
    try:
        queue = queue or task_queue.TaskQueue(queue_path)
        record = event_ingestion.enqueue_envelope(queue, envelope)
    except (sqlite3.Error, RuntimeError, ValueError) as exc:
        rollback_errors: list[str] = []
        if prepared is not None:
            rollback_errors = worktree_manager.rollback_prepared_task_branch(repository, prepared)
        rollback_note = f"; branch rollback failed: {'; '.join(rollback_errors)}" if rollback_errors else ""
        raise CLIError(f"task could not be queued: {exc}{rollback_note}") from exc
    service = worker_service_status(root)
    worker = service if service["alive"] else run_worker_command(root, "start", workers=3)
    stored_branch = str(record.payload.get("branch", branch))
    result = {
        "status": record.status,
        "queue_task_id": record.id,
        "task_id": task_id,
        "task_key": record.task_key,
        "run_id": record.run_id,
        "repository": str(repository),
        "branch": stored_branch,
        "workspace_mode": str(record.payload.get("workspace_mode", workspace_mode)),
        "mode": str(record.payload.get("mode", args.mode)),
        "queue_db": str(queue_path),
        "idempotent": record.payload.get("event_id") == envelope["event_id"],
        "worker": {
            "status": str(worker.get("status", "starting")),
            "pid": safe_int(worker.get("pid", 0)),
        },
        "warnings": branch_warnings,
    }
    collector = getattr(args, "result_collector", None)
    if callable(collector):
        collector(result)
        return 0
    emit(
        result,
        as_json=args.json,
        lines=(
            f"Task queued: {task_id}",
            f"  queue id: {record.id}",
            f"  base branch: {config.base_branch}",
            f"  branch: {stored_branch}",
            f"  workspace: {task_workspace_label(args)}",
            f"  mode: {record.payload.get('mode', args.mode)}",
            f"  status: {record.status}",
            f"  worker: {worker.get('status', 'starting')}",
            *(f"  warning: {warning}" for warning in branch_warnings),
            f"Follow it with: agent watch --repo {repository} --task-id {task_id}",
            "If input is needed, watch/status will print ATTENTION REQUIRED and the exact answer command.",
        ),
    )
    return 0


def handle_batch(args: argparse.Namespace) -> int:
    """Validate one YAML manifest and submit each bounded task through normal intake."""

    if args.file == "-":
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise CLIError("batch manifest must not exceed 1 MiB")
        source = raw
        base_dir = Path.cwd()
    else:
        path = Path(args.file).expanduser().resolve()
        try:
            if path.stat().st_size > 1_048_576:
                raise CLIError("batch manifest must not exceed 1 MiB")
            source = path.read_bytes()
        except OSError as exc:
            raise CLIError(f"cannot read batch manifest: {exc}") from exc
        base_dir = path.parent
    try:
        tasks = parse_batch_manifest(source, base_dir=base_dir)
    except BatchManifestError as exc:
        raise CLIError(str(exc)) from exc
    fingerprint = hashlib.sha256(source).hexdigest()[:12]
    batch_id = datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S.%fZ-batch-{fingerprint}")
    allowed_repositories = sorted({str(task["repository"]) for task in tasks})
    accepted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for task in tasks:
        item_result: list[dict[str, Any]] = []
        task_args = argparse.Namespace(
            goal=[str(task["goal"])],
            repo=str(task["repository"]),
            task_id=str(task["task_id"]),
            branch="",
            current_branch=False,
            worktree=bool(task["parallel"]),
            keep_paused=False,
            mode=str(task["mode"]),
            priority=int(task["priority"]),
            max_retries=int(task["max_retries"]),
            max_parallel_tasks=int(task["max_parallel_tasks"]),
            dry_run=bool(args.dry_run),
            json=True,
            batch_id=batch_id,
            batch_index=int(task["batch_index"]),
            allowed_child_repositories=allowed_repositories,
            result_collector=item_result.append,
        )
        try:
            handle_task(task_args)
        except CLIError as exc:
            errors.append(
                {
                    "index": task["batch_index"],
                    "repository": str(task["repository"]),
                    "error": str(exc),
                }
            )
            continue
        accepted.extend(item_result)
    payload = {
        "status": "accepted" if not errors else ("partial" if accepted else "error"),
        "batch_id": batch_id,
        "accepted": accepted,
        "errors": errors,
    }
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"Batch {batch_id}: {len(accepted)} task(s) accepted, {len(errors)} rejected.",
            *(f"  accepted: {item['task_id']} ({item['repository']})" for item in accepted),
            *(f"  error[{item['index']}]: {item['error']}" for item in errors),
        ),
    )
    return 0 if accepted else 1


def task_workspace_label(args: argparse.Namespace) -> str:
    if args.worktree:
        return "isolated worktree"
    if args.current_branch:
        return "existing branch in current checkout"
    return "dedicated branch in current checkout"


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def last_worker_error(root: Path) -> dict[str, Any]:
    path = root / ".agent-queue" / "worker-service.log"
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - 65_536)
            handle.seek(start)
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
            if start and lines:
                lines = lines[1:]
    except OSError:
        return {}
    for line in reversed(lines[-100:]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("error_type"):
            return {
                "error_type": str(value.get("error_type", "")),
                "message": str(value.get("message", "")),
                "time": str(value.get("time", "")),
            }
    return {}


def worker_service_status(root: Path) -> dict[str, Any]:
    state = read_json_object(root / ".agent-queue" / "worker-service.json")
    pid = safe_int(state.get("pid", 0))
    alive = process_alive(pid)
    recorded_status = str(state.get("status", "not_started"))
    current_build_fingerprint = harness_build_fingerprint(root)
    worker_build_fingerprint = str(state.get("build_fingerprint", ""))
    stale_build = bool(
        alive
        and worker_build_fingerprint
        and current_build_fingerprint
        and worker_build_fingerprint != current_build_fingerprint
    )
    if not state:
        status = "not_started"
    elif alive:
        status = recorded_status
    elif recorded_status in {"stopped", "stopping"}:
        status = "stopped"
    else:
        status = "unhealthy"
    return {
        "configured": bool(state),
        "alive": alive,
        "pid": pid,
        "service_id": str(state.get("service_id", "")),
        "status": status,
        "log": str(root / ".agent-queue" / "worker-service.log"),
        "last_error": last_worker_error(root) if status == "unhealthy" else {},
        "build_fingerprint": worker_build_fingerprint,
        "current_build_fingerprint": current_build_fingerprint,
        "stale_build": stale_build,
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
                "workspace_mode": str(task_payload.get("workspace_mode", "worktree")),
                "checkout_path": str(task_payload.get("checkout_path", repository_value)),
                "task_branch": str(task_payload.get("task_branch", task_payload.get("branch", ""))),
                "base_sha": str(task_payload.get("base_sha", "")),
                "branch_owner_run_id": str(task_payload.get("branch_owner_run_id", row["run_id"])),
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
                "cancellation_requested_at": (
                    float(row["cancellation_requested_at"])
                    if "cancellation_requested_at" in row.keys()
                    else 0.0
                ),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
            }
        )
    return selected


def workflow_attention(workflow: dict[str, Any]) -> dict[str, Any]:
    raw = workflow.get("attention")
    if isinstance(raw, dict) and raw.get("required") is True:
        details = raw.get("details", [])
        return {
            "required": True,
            "summary": str(raw.get("summary", "Attention is required.")),
            "details": [str(item) for item in details] if isinstance(details, list) else [],
            "role": str(raw.get("role", workflow.get("current_role", ""))),
            "action": str(raw.get("action", "")),
            "question": raw.get("question", {}) if isinstance(raw.get("question"), dict) else {},
            "repeated_question": raw.get("repeated_question") is True,
            "repeated_requirement": raw.get("repeated_requirement") is True,
        }
    status = str(workflow.get("execution_status", ""))
    if status not in {"awaiting_approval", "blocked", "dead_letter", "failed"}:
        return {
            "required": False,
            "summary": "",
            "details": [],
            "role": "",
            "action": "",
            "question": {},
            "repeated_question": False,
            "repeated_requirement": False,
        }
    values: list[str] = []
    blockers = workflow.get("blockers", [])
    if isinstance(blockers, list):
        values.extend(str(item) for item in blockers)
    values.append(str(workflow.get("recovery_reason", "")))
    roles = workflow.get("roles", [])
    if isinstance(roles, list):
        for checkpoint in reversed(roles):
            result = checkpoint.get("result") if isinstance(checkpoint, dict) else None
            if not isinstance(result, dict) or result.get("status") == "completed":
                continue
            values.insert(0, str(result.get("summary", "")))
            result_blockers = result.get("blockers", [])
            if isinstance(result_blockers, list):
                values.extend(str(item) for item in result_blockers)
            break
    details = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    return {
        "required": True,
        "summary": details[0] if details else status,
        "details": details[1:],
        "role": str(workflow.get("current_role", "")),
        # Older runs did not record what kind of attention they require. Treat
        # an unknown approval gate conservatively: an informational answer must
        # never stand in for a risk or security decision.
        "action": "approve" if status == "awaiting_approval" else "fix_then_retry",
        "question": {},
        "repeated_question": False,
        "repeated_requirement": False,
    }


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
        progress = read_json_object(run_dir / "progress.json")
        progress_time = str(progress.get("last_progress_at", ""))
        seconds_since_progress = 0
        if progress_time:
            try:
                seconds_since_progress = max(
                    0,
                    int(
                        datetime.now(timezone.utc).timestamp()
                        - datetime.fromisoformat(progress_time).timestamp()
                    ),
                )
            except ValueError:
                seconds_since_progress = 0
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
                "branch": str(workflow.get("task_branch", workflow.get("branch", ""))),
                "worktree": str(workflow.get("checkout_path", workflow.get("worktree", workflow.get("repository", "")))),
                "workspace_mode": str(workflow.get("workspace_mode", "worktree")),
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
                "attention": workflow_attention(workflow),
                "progress": {
                    "phase": str(progress.get("phase", workflow.get("execution_status", ""))),
                    "last_sdk_event": str(progress.get("last_sdk_event", "")),
                    "active_tool": str(progress.get("active_tool", "")),
                    "seconds_since_progress": seconds_since_progress,
                    "tokens_used": int(progress.get("tokens_used", workflow.get("tokens_used", 0)) or 0),
                    "token_budget": int(progress.get("token_budget", workflow.get("budgets", {}).get("max_tokens", 0)) or 0)
                    if isinstance(workflow.get("budgets", {}), dict)
                    else int(progress.get("token_budget", 0) or 0),
                    "stop_reason": str(progress.get("stop_reason", workflow.get("recovery_reason", ""))),
                    "thread_id": str(progress.get("thread_id", "")),
                    "execution_profile": str(progress.get("execution_profile", "")),
                    "model": str(progress.get("model", "")),
                    "reasoning_effort": str(progress.get("reasoning_effort", "")),
                },
            }
        )
    return selected


def git_ref_exists(repository: Path, ref: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", ref],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def available_base_ref(repository: Path, base_branch: str) -> str:
    for ref in (f"refs/remotes/origin/{base_branch}", f"refs/heads/{base_branch}"):
        if git_ref_exists(repository, ref):
            return ref.removeprefix("refs/remotes/").removeprefix("refs/heads/")
    return ""


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


def record_human_input(
    run_dir: Path,
    *,
    run_id: str,
    actor: str,
    response: str,
    attention: dict[str, Any] | None = None,
) -> Path:
    path = run_dir / "human-input.json"
    temporary = path.with_suffix(".json.tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise CLIError("refusing to write human input through a symbolic link")
    existing = read_json_object(path)
    entries = existing.get("entries", []) if isinstance(existing, dict) else []
    if not isinstance(entries, list):
        entries = []
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "actor": sanitized_message(actor, limit=128),
        "response": sanitized_message(response, limit=10_000),
    }
    if isinstance(attention, dict):
        entry["question_fingerprint"] = str(attention.get("fingerprint", ""))[:100]
        requirement = attention.get("requirement", {})
        if isinstance(requirement, dict):
            entry["requirement_id"] = sanitized_message(
                str(requirement.get("requirement_id", "")), limit=120
            )
        question = attention.get("question", {})
        if isinstance(question, dict):
            entry["question_id"] = sanitized_message(str(question.get("id", "")), limit=80)
    entries.append(entry)
    payload = {"version": 1, "run_id": run_id, "entries": entries[-50:]}
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def resolve_answer_attention(run_dir: Path) -> None:
    """Archive the answer and rerun the paused role with the new information."""
    path = run_dir / "workflow.json"
    temporary = path.with_suffix(".json.tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise CLIError("refusing to update workflow state through a symbolic link")
    workflow = read_json_object(path)
    attention = workflow.get("attention")
    if not isinstance(attention, dict) or attention.get("action") not in {"answer", "answer_or_approve"}:
        raise CLIError("the workflow question changed before the answer could be applied")
    attention_details = attention.get("details", [])
    active_values = {str(attention.get("summary", "")).strip()}
    if isinstance(attention_details, list):
        active_values.update(str(item).strip() for item in attention_details)
    active_values.discard("")
    history = workflow.get("attention_history", [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            **attention,
            "required": False,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "resolution": "answer_recorded",
        }
    )
    workflow["attention_history"] = history[-50:]
    requirement = attention.get("requirement", {})
    if isinstance(requirement, dict) and requirement.get("requirement_id"):
        closed = workflow.get("closed_requirements", [])
        if not isinstance(closed, list):
            closed = []
        requirement_id = str(requirement["requirement_id"])
        closed = [
            item
            for item in closed
            if not isinstance(item, dict)
            or str(item.get("requirement_id", "")) != requirement_id
        ]
        closed.append(
            {
                **requirement,
                "role": str(attention.get("role", "")),
                "fingerprint": str(attention.get("fingerprint", "")),
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "resolution": "answer_recorded",
            }
        )
        workflow["closed_requirements"] = closed[-50:]
    workflow.pop("attention", None)
    blockers = workflow.get("blockers", [])
    if isinstance(blockers, list):
        workflow["blockers"] = [
            item for item in blockers if str(item).strip() not in active_values
        ]
    role = str(workflow.get("current_role", ""))
    if role:
        checkpoint = read_checkpoint(run_dir, role)
        if checkpoint is not None:
            write_checkpoint(
                run_dir,
                RoleCheckpoint(
                    run_id=checkpoint.run_id,
                    role=checkpoint.role,
                    state="role_pending",
                    attempt=checkpoint.attempt,
                    worktree=checkpoint.worktree,
                    input_fingerprint=checkpoint.input_fingerprint,
                ),
            )
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def handle_answer(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise CLIError("project configuration is not locally trusted; run `agent init` again")
    response = " ".join(args.response).strip()
    if not response:
        raise CLIError("an answer is required")
    if len(response) > 10_000:
        raise CLIError("the answer must not exceed 10000 characters")
    root = harness_home()
    matching = [run for run in project_runs(root / ".agent-runs", repository) if run["run_id"] == args.run_id]
    if not matching:
        raise CLIError(f"run {args.run_id!r} was not found for this project")
    selected = matching[0]
    if selected["approval"]["status"] != "pending":
        raise CLIError("this run does not have a pending question or approval")
    run_dir = root / ".agent-runs" / args.run_id
    workflow = read_json_object(run_dir / "workflow.json")
    attention = workflow.get("attention", {})
    if not isinstance(attention, dict) or attention.get("action") not in {"answer", "answer_or_approve"}:
        raise CLIError(
            "this gate requires an explicit approval decision, not an informational answer; use `agent approve`"
        )
    record_human_input(
        run_dir,
        run_id=args.run_id,
        actor=args.actor,
        response=response,
        attention=attention,
    )
    approval_lifecycle = load_harness_module(root, "approval_lifecycle")
    task_queue = load_harness_module(root, "task_queue")
    try:
        approval = approval_lifecycle.approve_run(
            run_dir,
            actor=args.actor,
            reason="User supplied the information requested by the paused workflow.",
        )
        resolve_answer_attention(run_dir)
        transition, record = approval_lifecycle.resume_run(
            run_dir,
            queue=task_queue.TaskQueue(root / ".agent-queue" / "tasks.db"),
        )
    except approval_lifecycle.ApprovalError as exc:
        raise CLIError(str(exc)) from exc
    payload = {
        "status": "queued",
        "run_id": args.run_id,
        "approval_id": approval["approval_id"],
        "checkpoint_role": approval["checkpoint_role"],
        "queue_task_id": record.id,
        "task_id": record.payload.get("task_id", ""),
        "execution_status": transition["workflow"]["execution_status"],
        "answer_recorded": True,
    }
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"Answer recorded for {args.run_id}.",
            f"  checkpoint: {approval['checkpoint_role']}",
            "  the same run is queued to continue",
            f"Watch it with: agent watch --repo {repository} --run-id {args.run_id}",
        ),
    )
    return 0


def attention_items(
    tasks: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    runs_by_id = {str(run["run_id"]): run for run in runs if run.get("run_id")}
    items: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    for task in tasks:
        if not task["requires_human"] and task["status"] not in {
            "awaiting_approval",
            "blocked",
            "dead_letter",
            "failed",
        }:
            continue
        run_id = str(task.get("run_id", ""))
        if run_id and run_id in seen_runs:
            continue
        run = runs_by_id.get(run_id, {})
        attention = run.get("attention", {}) if isinstance(run, dict) else {}
        summary = str(task.get("exception_reason", "")).strip()
        if not summary and isinstance(attention, dict):
            summary = str(attention.get("summary", "")).strip()
        items.append(
            {
                "queue_task_id": task["queue_task_id"],
                "task_id": task["task_id"],
                "run_id": run_id,
                "status": task["status"],
                "role": str(run.get("current_role", "")) if isinstance(run, dict) else "",
                "summary": summary or "The task requires attention.",
                "details": list(attention.get("details", [])) if isinstance(attention, dict) else [],
                "action": str(attention.get("action", "")) if isinstance(attention, dict) else "",
                "question": dict(attention.get("question", {}))
                if isinstance(attention, dict) and isinstance(attention.get("question"), dict)
                else {},
                "repeated_question": bool(
                    isinstance(attention, dict) and attention.get("repeated_question") is True
                ),
                "approval_pending": bool(
                    isinstance(run, dict) and run.get("approval", {}).get("status") == "pending"
                ),
            }
        )
        if run_id:
            seen_runs.add(run_id)
    for run in runs:
        run_id = str(run.get("run_id", ""))
        attention = run.get("attention", {})
        if run_id in seen_runs or not isinstance(attention, dict) or not attention.get("required"):
            continue
        items.append(
            {
                "queue_task_id": 0,
                "task_id": run.get("task_id", ""),
                "run_id": run_id,
                "status": run.get("status", ""),
                "role": attention.get("role", run.get("current_role", "")),
                "summary": attention.get("summary", "The task requires attention."),
                "details": list(attention.get("details", [])),
                "action": str(attention.get("action", "")),
                "question": dict(attention.get("question", {}))
                if isinstance(attention.get("question"), dict)
                else {},
                "repeated_question": attention.get("repeated_question") is True,
                "approval_pending": run.get("approval", {}).get("status") == "pending",
            }
        )
    return items


def attention_output_lines(items: list[dict[str, Any]], repository: Path) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append(f"ATTENTION REQUIRED: {item['task_id']} [{item['status']}]")
        if item["role"]:
            lines.append(f"  role: {item['role']}")
        lines.append(f"  question/cause: {item['summary']}")
        for detail in item["details"]:
            if str(detail).strip() and str(detail).strip() != item["summary"]:
                lines.append(f"  needed: {detail}")
        run_id = str(item["run_id"])
        question = item.get("question", {})
        options = question.get("options", []) if isinstance(question, dict) else []
        if isinstance(options, list):
            for index, option in enumerate(options, start=1):
                if not isinstance(option, dict):
                    continue
                recommended = " (recommended)" if option.get("recommended") is True else ""
                description = str(option.get("description", "")).strip()
                suffix = f" — {description}" if description else ""
                lines.append(f"  option {index}: {option.get('label', '')}{recommended}{suffix}")
        if item["approval_pending"] and run_id and item.get("action") in {"answer", "answer_or_approve"}:
            lines.append(f'  answer: agent answer --repo {shlex.quote(str(repository))} {run_id} "Your answer"')
        elif item["approval_pending"] and run_id:
            lines.append(f"  approve if acceptable: agent approve --repo {shlex.quote(str(repository))} --run-id {run_id}")
        elif run_id:
            lines.append(f"  inspect: agent failures --repo {shlex.quote(str(repository))} --run-id {run_id}")
            lines.append(f"  after fixing the cause: agent retry --repo {shlex.quote(str(repository))} {run_id}")
    return lines


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
    attention = attention_items(tasks, runs)
    payload = {
        "project": {
            "id": config.project_id,
            "profile": config.profile,
            "repository": str(repository),
            "runtime_provider": config.runtime_provider,
            "base_branch": config.base_branch,
        },
        "queue": {"counts": task_counts, "items": tasks[: args.limit]},
        "runs": {"counts": run_counts, "items": runs[: args.limit]},
        "attention": attention,
        "worker_service": service,
    }
    lines = [
        f"Project: {config.project_id} ({config.profile})",
        f"Repository: {repository}",
        f"Base branch: {config.base_branch}",
        "Queue: " + (", ".join(f"{key}={value}" for key, value in sorted(task_counts.items())) or "empty"),
        "Runs: " + (", ".join(f"{key}={value}" for key, value in sorted(run_counts.items())) or "none"),
        f"Worker service: {service['status']} ({'running' if service['alive'] else 'not running'})",
    ]
    lines.extend(attention_output_lines(attention, repository))
    for item in tasks[: args.limit]:
        marker = " !" if item["requires_human"] else ""
        age_seconds = max(0, int(time.time() - item["created_at"]))
        lines.append(
            f"  task {item['queue_task_id']}: {item['task_id']} [{item['status']}]{marker} "
            f"workspace={item['workspace_mode']} age={age_seconds}s"
        )
        lines.append(
            f"    checkout: {item['checkout_path']}; branch: {item['task_branch'] or 'unknown'}; "
            f"base: {item['base_sha'] or 'pending'}; owner: {item['branch_owner_run_id'] or item['run_id'] or 'pending'}"
        )
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
        if item["cancellation_requested_at"]:
            lines.append("    cancellation requested; worker is terminating the process group")
    for run in runs[: args.limit]:
        progress = run.get("progress", {})
        if isinstance(progress, dict) and run["status"] not in {"completed", "cancelled"}:
            lines.append(
                f"  run {run['run_id']}: phase={progress.get('phase') or run['status']} "
                f"event={progress.get('last_sdk_event') or '-'} tool={progress.get('active_tool') or '-'} "
                f"idle={progress.get('seconds_since_progress', 0)}s "
                f"budget={progress.get('tokens_used', 0)}/{progress.get('token_budget', 0)} "
                f"profile={progress.get('execution_profile') or '-'}"
            )
            if progress.get("stop_reason"):
                lines.append(f"    stop reason: {progress['stop_reason']}")
        if run["status"] in {"retry_wait", "repairing", "resuming", "dead_letter", "failed"}:
            lines.append(
                f"  run {run['run_id']}: {run['status']} role={run['current_role'] or 'unknown'} "
                f"failure={run['failure_kind'] or 'unknown'} action={run['recovery_action'] or 'none'} "
                f"attempt={run['failure_attempt']}/{run['failure_max_attempts']} "
                f"checkpoint={run['resume_from'] or 'none'}"
            )
            lines.append(
                f"    branch: {run['branch'] or 'unknown'}; worktree: {run['worktree'] or 'unknown'}; "
                f"workspace: {run['workspace_mode']}"
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
        run_attention = run.get("attention", {})
        if isinstance(run_attention, dict) and run_attention.get("action") in {"answer", "answer_or_approve"}:
            lines.append("    answer the question shown above; approval alone cannot supply missing information")
        elif run["approval"]["registration_required"]:
            lines.append("    publication requires trusted repository registration")
        else:
            lines.append(f"    agent approve --repo {repository} --run-id {run['run_id']}")
    if task_counts.get("queued", 0) and not service["alive"]:
        lines.append(f"Next: agent start --repo {repository}")
    elif not tasks and not runs:
        lines.append('Next: agent task "Describe the change"')
    emit(payload, as_json=args.json, lines=lines)
    return 0


def handle_watch(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise CLIError("project configuration is not locally trusted; run `agent init` again")
    if args.interval < 0.2 or args.interval > 60:
        raise CLIError("--interval must be between 0.2 and 60 seconds")
    if args.timeout < 0:
        raise CLIError("--timeout must be zero or positive")
    root = harness_home()
    started = time.monotonic()
    last_signature = ""
    while True:
        tasks = project_tasks(root / ".agent-queue" / "tasks.db", repository)
        runs = project_runs(root / ".agent-runs", repository)
        if args.task_id:
            tasks = [item for item in tasks if item["task_id"] == args.task_id]
        if args.run_id:
            tasks = [item for item in tasks if item["run_id"] == args.run_id]
            runs = [item for item in runs if item["run_id"] == args.run_id]
        selected_task = tasks[0] if tasks else None
        selected_run = None
        if selected_task and selected_task["run_id"]:
            selected_run = next(
                (run for run in runs if run["run_id"] == selected_task["run_id"]),
                None,
            )
        elif runs:
            selected_run = runs[0]
        if selected_task is None and selected_run is None:
            target = args.run_id or args.task_id or "latest task"
            raise CLIError(f"{target!r} was not found for this project")
        task_status = str(selected_task["status"]) if selected_task else ""
        run_status = str(selected_run["status"]) if selected_run else ""
        role = str(selected_run["current_role"]) if selected_run else ""
        run_id = str(selected_run["run_id"]) if selected_run else str(selected_task["run_id"])
        task_id = str(selected_task["task_id"]) if selected_task else str(selected_run["task_id"])
        attention = attention_items(
            [selected_task] if selected_task else [],
            [selected_run] if selected_run else [],
        )
        snapshot = {
            "task_id": task_id,
            "run_id": run_id,
            "queue_status": task_status,
            "run_status": run_status,
            "current_role": role,
            "attention": attention,
            "progress": selected_run.get("progress", {}) if selected_run else {},
        }
        signature = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
        if not args.json and signature != last_signature:
            print(
                f"Task {task_id}: queue={task_status or '-'} run={run_status or '-'} "
                f"role={role or '-'}"
            )
            progress = snapshot["progress"]
            if isinstance(progress, dict) and progress:
                print(
                    f"  phase={progress.get('phase') or '-'} event={progress.get('last_sdk_event') or '-'} "
                    f"tool={progress.get('active_tool') or '-'} idle={progress.get('seconds_since_progress', 0)}s "
                    f"budget={progress.get('tokens_used', 0)}/{progress.get('token_budget', 0)} "
                    f"profile={progress.get('execution_profile') or '-'} "
                    f"stop={progress.get('stop_reason') or '-'}"
                )
            last_signature = signature
        if attention:
            emit(
                snapshot,
                as_json=args.json,
                lines=attention_output_lines(attention, repository),
            )
            return 0
        service = worker_service_status(root)
        if task_status not in {"completed", "cancelled"} and run_status != "completed" and not service["alive"]:
            emit(
                {**snapshot, "worker_service": service},
                as_json=args.json,
                lines=(
                    f"Task {task_id} is waiting because the worker service is not running.",
                    f"Start it with: agent start --repo {repository}",
                ),
            )
            return 0
        terminal = task_status in {"completed", "cancelled"} or run_status == "completed"
        if terminal:
            emit(
                snapshot,
                as_json=args.json,
                lines=(f"Task {task_id} finished with status {task_status or run_status}.",),
            )
            return 0
        if args.timeout and time.monotonic() - started >= args.timeout:
            emit(
                snapshot,
                as_json=args.json,
                lines=(
                    f"Watch timeout reached; task {task_id} is still {task_status or run_status}.",
                    "Run the same agent watch command to continue following it.",
                ),
            )
            return 0
        time.sleep(args.interval)


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
        run_id = str(item.get("run_id", ""))
        lines.append(
            f"  {run_id}: {item.get('kind', '')}/{item.get('error_type', '')} "
            f"attempt {item.get('attempt', 0)}/{item.get('max_attempts', 0)} at {item.get('role', '') or item.get('stage', '')}"
        )
        if item.get("message"):
            lines.append(f"    cause: {item['message']}")
        if item.get("retryable"):
            lines.append(f"    after fixing the cause: agent retry {run_id}")
        elif item.get("checkpoint"):
            lines.append(f"    recorded checkpoint: {item['checkpoint']}; inspect `agent status` before resuming")
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
            *(
                f"  {item['run_id'] or item['task_id']}: {item['exception_reason'] or item['failure_kind']}"
                for item in items[: args.limit]
            ),
            *(
                f"    inspect: agent failures --run-id {item['run_id']}"
                for item in items[: args.limit]
                if item["run_id"]
            ),
        ),
    )
    return 0


def worker_subprocess_error(completed: subprocess.CompletedProcess[str]) -> str:
    output = (completed.stderr or completed.stdout or "worker service command failed").strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if any("Traceback (most recent call last)" in line for line in lines):
        cause = lines[-1] if lines else "worker service failed"
        return f"{cause}; {dependency_repair_hint()}"
    return output


def run_worker_command(root: Path, action: str, *, workers: int = 0) -> dict[str, Any]:
    state = read_json_object(root / ".agent-queue" / "worker-service.json")
    command = [sys.executable, str(root / "scripts" / "worker_service.py"), action]
    if action in {"start", "restart"}:
        if state.get("db"):
            command.extend(["--db", str(state["db"])])
        selected_workers = workers
        if selected_workers <= 0:
            recorded_workers = state.get("workers")
            selected_workers = len(recorded_workers) if isinstance(recorded_workers, list) else 3
        command.extend(["--workers", str(selected_workers or 3)])
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CLIError(f"worker {action} failed: {exc}") from exc
    if completed.returncode != 0:
        raise CLIError(worker_subprocess_error(completed))
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = worker_service_status(root)
    return payload if isinstance(payload, dict) else worker_service_status(root)


def handle_worker_command(args: argparse.Namespace) -> int:
    root = harness_home()
    if args.worker_command == "status":
        status = worker_service_status(root)
        emit(
            status,
            as_json=args.json,
            lines=(
                f"Worker service: {status['status']}",
                f"  alive: {status['alive']}",
                f"  pid: {status['pid']}",
                f"  service: {status['service_id'] or '-'}",
            ),
        )
        return 0 if status["alive"] else 1
    payload = run_worker_command(root, args.worker_command, workers=int(getattr(args, "workers", 0) or 0))
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"Worker service {args.worker_command}: {payload.get('status', 'requested')}",
            f"  pid: {payload.get('pid', '-')}",
            f"  log: {root / '.agent-queue' / 'worker-service.log'}",
        ),
    )
    return 0


def handle_start(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise CLIError("project configuration is not locally trusted; run `agent init` again")
    missing = missing_runtime_imports()
    if missing:
        raise CLIError(f"missing runtime dependencies: {', '.join(missing)}; {dependency_repair_hint()}")
    base_ref = available_base_ref(repository, config.base_branch)
    if not base_ref:
        raise CLIError(
            f"configured base branch {config.base_branch!r} was not found locally or under origin; "
            "fetch it or run `agent init --force --base-branch <branch>`"
        )
    root = harness_home()
    payload = run_worker_command(root, "start", workers=args.workers)
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"AI Harness started for {config.project_id}.",
            f"  worker: {payload.get('status', 'starting')}",
            f"  base branch: {config.base_branch} ({base_ref})",
            f"  log: {root / '.agent-queue' / 'worker-service.log'}",
            'Next: agent task "Describe the change"',
        ),
    )
    return 0


def handle_stop(args: argparse.Namespace) -> int:
    root = harness_home()
    payload = run_worker_command(root, "stop")
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"AI Harness stop: {payload.get('status', 'requested')}",
            "Use `agent worker status` to confirm shutdown.",
        ),
    )
    return 0


def handle_dashboard(args: argparse.Namespace) -> int:
    repository = repository_from_arg(args.repo, require_initialized=True)
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise CLIError("project configuration is not locally trusted; run `agent init` again")
    root = harness_home()
    control_plane = load_harness_module(root, "control_plane_api")
    token = secrets.token_urlsafe(24)

    def ready(port: int) -> None:
        fragment = urllib.parse.urlencode({"token": token, "repo": str(repository)})
        url = f"http://127.0.0.1:{port}/dashboard#{fragment}"
        print(f"Dashboard ready: {url.split('#', 1)[0]}", flush=True)
        print("Press Ctrl+C in this Terminal window to stop it.", flush=True)
        if not args.no_open:
            webbrowser.open(url)

    try:
        control_plane.serve_control_plane(
            host="127.0.0.1",
            port=args.port,
            db_path=root / ".agent-queue" / "tasks.db",
            runs_dir=root / ".agent-runs",
            auth_token=token,
            default_repository=repository,
            on_ready=ready,
        )
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    except OSError as exc:
        raise CLIError(
            f"dashboard could not start on port {args.port}: {exc}; "
            "choose another port with `agent dashboard --port 8766`"
        ) from exc
    return 0


def dashboard_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be a number from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be a number from 1 to 65535")
    return port


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
        write_workflow = True
        if args.command == "abort":
            record = queue.abort_run(args.run_id)
            if record.status in {"claimed", "leased", "running"}:
                write_workflow = False
            else:
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
        if write_workflow:
            temporary = workflow_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.replace(workflow_path)
    except (ValueError, RuntimeError) as exc:
        raise CLIError(str(exc)) from exc
    reported_status = (
        "cancellation_requested"
        if args.command == "abort" and record.status in {"claimed", "leased", "running"}
        else record.status
    )
    emit(
        {"status": reported_status, "run_id": args.run_id, "queue_task_id": record.id, "action": args.command},
        as_json=args.json,
        lines=(f"Run {args.run_id}: {reported_status}", f"  action: {args.command}"),
    )
    return 0


def update_process(
    command: Sequence[str],
    *,
    label: str,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded install/update step with a concise user-facing error."""

    try:
        completed = subprocess.run(
            list(command),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CLIError(f"{label} failed: {exc}") from exc
    if completed.returncode != 0:
        detail = sanitized_message(
            (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip(),
            limit=1500,
        )
        raise CLIError(f"{label} failed: {detail}")
    return completed


def pipx_executable() -> str:
    configured = os.environ.get("AI_HARNESS_PIPX", "").strip()
    executable = configured or shutil.which("pipx") or ""
    if not executable:
        raise CLIError("pipx is not available; download the system and run `./install.sh` again")
    return executable


def pipx_installed_source(pipx: str) -> str:
    completed = update_process([pipx, "list", "--json"], label="reading installation information", timeout=30)
    try:
        document = json.loads(completed.stdout)
        source = document["venvs"]["ai-harness"]["metadata"]["main_package"]["package_or_url"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CLIError(
            "this installation is not managed by pipx; download the system and run `./install.sh` once"
        ) from exc
    if not isinstance(source, str) or not source.strip():
        raise CLIError("pipx did not report the installed package source; run `./install.sh` again")
    return source.strip()


def local_update_source(value: str) -> Path | None:
    candidate = Path(value).expanduser()
    return candidate.resolve() if candidate.is_dir() else None


def validate_local_update_source(source: Path) -> None:
    pyproject = source / "pyproject.toml"
    try:
        document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CLIError(f"{source} is not an AI Harness download: {exc}") from exc
    project = document.get("project", {})
    if not isinstance(project, dict) or project.get("name") != "ai-harness":
        raise CLIError(f"{source} is not an AI Harness download")


def refresh_git_source(source: Path) -> None:
    status = update_process(
        ["git", "-C", str(source), "status", "--porcelain=v1", "--untracked-files=normal"],
        label="checking the downloaded system",
        timeout=30,
    )
    dirty = [line[3:].strip() for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        visible = ", ".join(dirty[:5])
        raise CLIError(
            f"update stopped because the downloaded system has local changes: {visible}; "
            "commit or stash them, then run `agent update` again"
        )
    update_process(
        ["git", "-C", str(source), "pull", "--ff-only"],
        label="downloading the update",
        timeout=180,
    )


def installed_agent_executable() -> str:
    adjacent = Path(sys.executable).with_name("agent")
    if adjacent.is_file() and os.access(adjacent, os.X_OK):
        return str(adjacent)
    executable = shutil.which("agent")
    if not executable:
        raise CLIError("the updated agent executable was not found; run `./install.sh` again")
    return executable


def selected_update_source(requested: str, installed: str) -> tuple[str, Path | None, str]:
    """Return install spec, optional local path, and user-facing source kind."""

    if requested:
        local = local_update_source(requested)
        if local is not None:
            validate_local_update_source(local)
            return str(local), local, "downloaded folder"
        if requested.startswith(("git+https://", "git+ssh://")):
            return requested, None, "Git repository"
        raise CLIError("--source must be an AI Harness folder or a git+https/git+ssh URL")
    local = local_update_source(installed)
    if local is not None:
        validate_local_update_source(local)
        if (local / ".git").exists():
            return str(local), local, "Git checkout"
        return DEFAULT_UPDATE_SOURCE, None, "official repository"
    return installed, None, "installed package source"


def pause_worker_for_update() -> tuple[Path | None, bool]:
    """Stop a running worker before replacing its installed code."""

    try:
        root = harness_home()
    except HarnessNotFoundError:
        return None, False
    if not worker_service_status(root)["alive"]:
        return root, False
    run_worker_command(root, "stop")
    return root, True


def restore_worker_after_failed_update(root: Path | None, was_running: bool) -> str:
    if root is None or not was_running:
        return ""
    try:
        run_worker_command(root, "start")
    except CLIError as exc:
        return f"; the previous worker could not be restarted: {exc}"
    return "; the previous worker was restarted"


def handle_update(args: argparse.Namespace) -> int:
    pipx = pipx_executable()
    installed_source = pipx_installed_source(pipx)
    install_spec, local_source, source_kind = selected_update_source(args.source, installed_source)
    git_updated = False
    if local_source is not None and (local_source / ".git").exists():
        refresh_git_source(local_source)
        git_updated = True
    worker_root, worker_was_running = pause_worker_for_update()
    try:
        if local_source is not None or args.source or install_spec == DEFAULT_UPDATE_SOURCE:
            update_process(
                [pipx, "install", "--force", install_spec],
                label="installing the update",
                timeout=300,
            )
        else:
            update_process(
                [pipx, "upgrade", "--force", "ai-harness"],
                label="installing the update",
                timeout=300,
            )
    except CLIError as exc:
        recovery = restore_worker_after_failed_update(worker_root, worker_was_running)
        raise CLIError(f"{exc}{recovery}") from exc
    agent_executable = installed_agent_executable()
    version = update_process(
        [agent_executable, "--version"],
        label="verifying the updated command",
        timeout=30,
    ).stdout.strip()
    try:
        update_process(
            [agent_executable, "worker", "restart", "--json"],
            label="restarting the background service",
            timeout=180,
        )
    except CLIError as exc:
        payload = {
            "status": "updated_with_warning",
            "version": version,
            "source": source_kind,
            "git_updated": git_updated,
            "worker_restarted": False,
            "warning": str(exc),
        }
        emit(
            payload,
            as_json=args.json,
            lines=(
                f"AI Harness updated: {version}",
                f"  source: {source_kind}",
                f"  warning: {exc}",
                "Next: agent worker restart",
            ),
        )
        return 1
    payload = {
        "status": "updated",
        "version": version,
        "source": source_kind,
        "git_updated": git_updated,
        "worker_restarted": True,
    }
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"AI Harness updated: {version}",
            f"  source: {source_kind}",
            "  background service: restarted",
            "Next: open your project and run `agent doctor --full`",
        ),
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
        executable_path = Path(executable)
        if (
            (executable_path.is_file() and os.access(executable_path, os.X_OK))
            or shutil.which(executable)
        ):
            return configured
        return ""
    candidates = (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("codex") or ""


def handle_doctor(args: argparse.Namespace) -> int:
    checks: list[DoctorCheck] = []
    next_actions: list[str] = []
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
    if root is not None:
        try:
            load_recovery_policy(root / ".agent-recovery.yaml")
            checks.append(DoctorCheck("recovery_policy", "pass", str(root / ".agent-recovery.yaml")))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            checks.append(DoctorCheck("recovery_policy", "fail", str(exc)))
            next_actions.append("agent update")
    config = None
    git_ready = False
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
            if not trusted:
                next_actions.append(f"agent init --repo {repository}")
        except ProjectConfigError as exc:
            checks.append(DoctorCheck("project_config", "fail", str(exc)))
            next_actions.append(f"agent init --repo {repository}")
        try:
            git = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=repository,
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            git_ready = git.returncode == 0 and git.stdout.strip() == "true"
            git_detail = "git worktree" if git_ready else (git.stderr.strip() or "not a git repository")
        except (OSError, subprocess.TimeoutExpired) as exc:
            git_ready = False
            git_detail = f"git check failed: {exc}"
        checks.append(
            DoctorCheck(
                "git_repository",
                "pass" if git_ready else "warn",
                git_detail,
            )
        )
        if not git_ready:
            next_actions.append("initialize and commit the project as a Git repository")
        if config is not None and git_ready:
            base_ref = available_base_ref(repository, config.base_branch)
            checks.append(
                DoctorCheck(
                    "base_branch",
                    "pass" if base_ref else "fail",
                    f"{config.base_branch} ({base_ref})"
                    if base_ref
                    else (
                        f"{config.base_branch!r} was not found locally or under origin; fetch it or update "
                        "the project config with `agent init --force --base-branch <branch>`"
                    ),
                )
            )
            if not base_ref:
                next_actions.append("fetch the configured base branch or select an existing branch")
            try:
                checkout_status = subprocess.run(
                    ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
                    cwd=repository,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                dirty_paths = [line[3:] for line in checkout_status.stdout.splitlines() if line]
                checkout_clean = checkout_status.returncode == 0 and not dirty_paths
                checkout_detail = (
                    "clean; task branch can be prepared"
                    if checkout_clean
                    else "uncommitted changes: " + ", ".join(dirty_paths[:5])
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                checkout_clean = False
                checkout_detail = f"could not inspect checkout: {exc}"
            checks.append(
                DoctorCheck(
                    "git_checkout",
                    "pass" if checkout_clean else "warn",
                    checkout_detail,
                )
            )
            if not checkout_clean:
                next_actions.append("commit or stash project changes before `agent task`")
    missing = missing_runtime_imports()
    checks.append(
        DoctorCheck(
            "python_runtime",
            "fail" if missing else "pass",
            f"missing: {', '.join(missing)}; {dependency_repair_hint()}"
            if missing
            else f"Python {sys.version_info.major}.{sys.version_info.minor}; runtime imports available",
        )
    )
    if missing:
        next_actions.append("agent update")
    codex = configured_codex_command()
    checks.append(
        DoctorCheck(
            "codex_cli",
            "pass" if codex else "fail",
            codex or "Codex CLI was not found",
        )
    )
    if args.full and root is not None and repository is not None and codex and not missing:
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
            raw_detail = probe.stdout.strip() or probe.stderr.strip() or f"exit {probe.returncode}"
            try:
                preflight = json.loads(probe.stdout)
            except json.JSONDecodeError:
                preflight = {}
            if isinstance(preflight, dict) and preflight:
                blockers = preflight.get("blockers", [])
                warnings = preflight.get("warnings", [])
                runtime = preflight.get("runtime", {})
                runtime_provider = runtime.get("provider", "runtime") if isinstance(runtime, dict) else "runtime"
                if probe.returncode == 0:
                    detail = (
                        f"{runtime_provider} ready; "
                        f"sandbox={preflight.get('sandbox', 'unknown')}; repository accessible"
                    )
                    if isinstance(warnings, list) and warnings:
                        detail += f"; warning: {warnings[0]}"
                elif isinstance(blockers, list) and blockers:
                    detail = str(blockers[0])
                else:
                    detail = raw_detail
            else:
                detail = raw_detail
            checks.append(
                DoctorCheck("runtime_preflight", "pass" if probe.returncode == 0 else "fail", detail)
            )
        except subprocess.TimeoutExpired:
            checks.append(DoctorCheck("runtime_preflight", "fail", "runtime preflight timed out after 90s"))
    if root is not None:
        if (
            repository is not None
            and repository.resolve() != root.resolve()
            and (repository / "scripts" / "worker_service.py").is_file()
            and (repository / "ai_harness" / "build.py").is_file()
        ):
            installed_fingerprint = harness_build_fingerprint(root)
            source_fingerprint = harness_build_fingerprint(repository)
            source_synced = installed_fingerprint == source_fingerprint
            checks.append(
                DoctorCheck(
                    "installed_build",
                    "pass" if source_synced else "fail",
                    "installed harness matches this source checkout"
                    if source_synced
                    else "installed harness differs from this source checkout; run `agent update --source .`",
                )
            )
            if not source_synced:
                next_actions.append(f"agent update --source {repository}")
        service = worker_service_status(root)
        service_detail = (
            f"running pid={service['pid']}"
            if service["alive"]
            else f"{service['status']}; tasks remain queued; run `agent start --repo {repository or Path.cwd()}`"
        )
        if service["last_error"]:
            service_detail += (
                f"; last error: {service['last_error'].get('error_type', '')}: "
                f"{service['last_error'].get('message', '')}"
            )
        worker_status = "pass" if service["alive"] and not service["stale_build"] else (
            "fail" if service["stale_build"] else "warn"
        )
        if service["stale_build"]:
            service_detail += "; running worker uses an older installed build"
            next_actions.append("agent restart")
        elif service["alive"] and not service["build_fingerprint"]:
            worker_status = "warn"
            service_detail += "; restart once to record the worker build fingerprint"
            next_actions.append("agent restart")
        checks.append(
            DoctorCheck(
                "worker_service",
                worker_status,
                service_detail,
            )
        )
        if not service["alive"]:
            next_actions.append(f"agent start --repo {repository or Path.cwd()}")
    failed = any(check.status == "fail" for check in checks)
    warned = any(check.status == "warn" for check in checks)
    next_actions = list(dict.fromkeys(next_actions))
    payload = {
        "status": "fail" if failed else "pass",
        "version": __version__,
        "checks": [asdict(check) for check in checks],
        "next_actions": next_actions,
    }
    emit(
        payload,
        as_json=args.json,
        lines=(
            f"AI Harness {__version__}: {'problems found' if failed else ('ready with warnings' if warned else 'ready')}",
            *(f"  [{check.status.upper()}] {check.name}: {check.detail}" for check in checks),
            *(f"Next: {action}" for action in next_actions),
        ),
    )
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update", help="download and install the latest system update")
    update_parser.add_argument(
        "--source",
        default="",
        help="updated AI Harness folder or git+https/git+ssh source (normally not needed)",
    )
    update_parser.add_argument("--json", action="store_true")
    update_parser.set_defaults(handler=handle_update)

    init_parser = subparsers.add_parser("init", help="initialize a local project")
    init_parser.add_argument("--repo", default="", help="project directory (default: current directory)")
    init_parser.add_argument("--project-id", default="")
    init_parser.add_argument("--profile", choices=["auto", *sorted(SUPPORTED_PROFILES)], default="auto")
    init_parser.add_argument("--base-branch", default="auto")
    init_parser.add_argument(
        "--branch-prefix",
        default="",
        help="safe Git branch prefix for generated task branches (for example feat/, chore/, team/mobile/)",
    )
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument(
        "--replace-agents",
        action="store_true",
        help="replace an existing AGENTS.md as well as project configuration",
    )
    init_parser.add_argument("--json", action="store_true")
    init_parser.set_defaults(handler=handle_init)

    task_parser = subparsers.add_parser("task", help="enqueue a task for the current project")
    task_parser.add_argument("goal", nargs="+")
    task_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    task_parser.add_argument("--task-id", default="")
    task_parser.add_argument("--branch", default="")
    task_parser.add_argument(
        "--current-branch",
        action="store_true",
        help="use the clean already checked-out non-default branch instead of creating a task branch",
    )
    task_parser.add_argument(
        "--worktree",
        action="store_true",
        help="opt into an isolated Git worktree (useful for parallel tasks)",
    )
    task_parser.add_argument(
        "--keep-paused",
        action="store_true",
        help="do not replace a paused task that owns the current checkout",
    )
    task_parser.add_argument(
        "--mode",
        choices=("auto", "adaptive", "fast", "full", "goal"),
        default="auto",
        help=(
            "select current automatic routing, opt into the auditable adaptive plan, request the "
            "15-minute fast path, run the full role chain for up to 60 minutes, or explicitly run "
            "a checkpointed goal for up to 4 hours"
        ),
    )
    task_parser.add_argument("--priority", type=int, choices=range(-100, 101), default=0)
    task_parser.add_argument("--max-retries", type=int, choices=range(0, 11), default=2)
    task_parser.add_argument(
        "--max-parallel-tasks",
        type=int,
        choices=range(1, 33),
        default=0,
        help=argparse.SUPPRESS,
    )
    task_parser.add_argument("--batch-id", default="", help=argparse.SUPPRESS)
    task_parser.add_argument("--batch-index", type=int, default=0, help=argparse.SUPPRESS)
    task_parser.add_argument(
        "--allowed-child-repository",
        dest="allowed_child_repositories",
        action="append",
        default=[],
        help=argparse.SUPPRESS,
    )
    task_parser.add_argument("--dry-run", action="store_true")
    task_parser.add_argument("--json", action="store_true")
    task_parser.set_defaults(handler=handle_task)

    batch_parser = subparsers.add_parser(
        "batch", help="enqueue a validated YAML batch across initialized projects"
    )
    batch_parser.add_argument("--file", required=True, help="YAML manifest path or - for stdin")
    batch_parser.add_argument("--dry-run", action="store_true")
    batch_parser.add_argument("--json", action="store_true")
    batch_parser.set_defaults(handler=handle_batch)

    start_parser = subparsers.add_parser("start", help="validate this project and start autonomous workers")
    start_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    start_parser.add_argument("--workers", type=int, choices=range(1, 33), default=3)
    start_parser.add_argument("--json", action="store_true")
    start_parser.set_defaults(handler=handle_start)

    stop_parser = subparsers.add_parser("stop", help="stop the autonomous worker service")
    stop_parser.add_argument("--json", action="store_true")
    stop_parser.set_defaults(handler=handle_stop)

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="open the local task launch and control dashboard",
    )
    dashboard_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    dashboard_parser.add_argument("--port", type=dashboard_port, metavar="PORT", default=8765)
    dashboard_parser.add_argument("--no-open", action="store_true", help="start the server without opening a browser")
    dashboard_parser.set_defaults(handler=handle_dashboard)

    status_parser = subparsers.add_parser("status", help="show compact project queue and run status")
    status_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    status_parser.add_argument("--limit", type=int, choices=range(1, 101), default=10)
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=handle_status)

    watch_parser = subparsers.add_parser("watch", help="follow one task until completion or user attention")
    watch_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    watch_parser.add_argument("--task-id", default="")
    watch_parser.add_argument("--run-id", default="")
    watch_parser.add_argument("--interval", type=float, default=2.0)
    watch_parser.add_argument(
        "--timeout",
        type=float,
        default=1800.0,
        help="seconds (default: 1800); zero waits indefinitely",
    )
    watch_parser.add_argument("--json", action="store_true")
    watch_parser.set_defaults(handler=handle_watch)

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

    worker_parser = subparsers.add_parser("worker", help="inspect or control the persistent worker service")
    worker_subparsers = worker_parser.add_subparsers(dest="worker_command", required=True)
    for worker_command in ("status", "start", "restart", "stop"):
        worker_action = worker_subparsers.add_parser(worker_command)
        if worker_command in {"start", "restart"}:
            worker_action.add_argument("--workers", type=int, choices=range(1, 33), default=0)
        worker_action.add_argument("--json", action="store_true")
        worker_action.set_defaults(handler=handle_worker_command)

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

    answer_parser = subparsers.add_parser(
        "answer",
        help="answer a paused workflow question and resume the same run",
    )
    answer_parser.add_argument("run_id")
    answer_parser.add_argument("response", nargs="+")
    answer_parser.add_argument("--repo", default="", help="project directory (default: discover from cwd)")
    answer_parser.add_argument("--actor", default="user")
    answer_parser.add_argument("--json", action="store_true")
    answer_parser.set_defaults(handler=handle_answer)

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
    except ImportError as exc:
        missing = str(getattr(exc, "name", "") or "unknown Python module")
        message = f"required Python module {missing!r} is unavailable; {dependency_repair_hint()}"
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": message}, ensure_ascii=False))
        else:
            print(f"error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
