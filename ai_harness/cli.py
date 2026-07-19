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
        selected.append(
            {
                "run_id": run_dir.name,
                "task_id": str(workflow.get("task_id", "")),
                "status": str(workflow.get("execution_status", "unknown")),
                "role_count": int(workflow.get("role_count", 0) or 0),
                "tokens_used": int(workflow.get("tokens_used", 0) or 0),
                "branch": str(workflow.get("branch", "")),
                "blockers": [str(item) for item in workflow.get("blockers", [])],
            }
        )
    return selected


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
        f"Worker service: {'running' if service['alive'] else 'not running'}",
    ]
    for item in tasks[: args.limit]:
        marker = " !" if item["requires_human"] else ""
        lines.append(f"  task {item['queue_task_id']}: {item['task_id']} [{item['status']}]{marker}")
    emit(payload, as_json=args.json, lines=lines)
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
