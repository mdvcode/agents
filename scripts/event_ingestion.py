#!/usr/bin/env python3
"""Normalize CLI, GitHub, webhook, API, and CI events into one queued Task."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_contracts import load_json, validate_contract
from task_queue import DEFAULT_DB, TaskQueue, TaskRecord


ROOT = Path(__file__).resolve().parents[1]
EVENTS_DIR = ROOT / ".agent-queue" / "events"
SOURCES = {"cli", "github_issue", "webhook", "api", "ci"}
TASK_SCHEMA = ROOT / "schemas" / "task_envelope.schema.json"


class EventError(ValueError):
    """Raised when an external event cannot become a safe Task."""


def text(value: Any, default: str = "") -> str:
    return str(value).strip() if isinstance(value, (str, int)) else default


def event_identity(source: str, external_id: str, task_id: str) -> str:
    return hashlib.sha256(f"{source}:{external_id}:{task_id}".encode("utf-8")).hexdigest()


def normalize_event(
    *,
    source: str,
    payload: dict[str, Any],
    repository: Path,
    project: str = "agent_workspace",
) -> dict[str, Any]:
    if source not in SOURCES:
        raise EventError(f"unsupported event source: {source}")
    repository = repository.resolve()
    if not repository.is_dir():
        raise EventError("event repository must be an existing local directory")
    issue = payload.get("issue", {}) if isinstance(payload.get("issue"), dict) else {}
    workflow_run = payload.get("workflow_run", {}) if isinstance(payload.get("workflow_run"), dict) else {}
    external_id = text(
        payload.get("external_id")
        or issue.get("id")
        or workflow_run.get("id")
        or payload.get("delivery_id")
        or payload.get("id")
    )
    if not external_id:
        raise EventError("event external id is required for idempotency")
    issue_number = text(issue.get("number"))
    task_id = text(payload.get("task_id"), f"issue-{issue_number}" if issue_number else f"event-{external_id}")
    title = text(payload.get("goal") or issue.get("title") or payload.get("title"), task_id)
    body = text(issue.get("body") or payload.get("body"))
    goal = title if not body else f"{title}\n\n{body}"
    branch = text(payload.get("branch") or workflow_run.get("head_branch"), f"issue/{task_id}")
    base_branch = text(payload.get("base_branch"), "main")
    workspace_mode = text(payload.get("workspace_mode"), "worktree")
    workspace_mode = {
        "isolated": "worktree",
        "current_branch": "checkout",
        "new_branch": "checkout",
    }.get(workspace_mode, workspace_mode)
    if workspace_mode not in {"checkout", "worktree"}:
        raise EventError("event workspace_mode must be checkout or worktree")
    mode = text(payload.get("mode"), "auto")
    if mode not in {"auto", "adaptive", "fast", "full", "goal"}:
        raise EventError("event mode must be auto, adaptive, fast, full, or goal")
    runtime_provider = text(payload.get("runtime_provider"), "codex-sdk")
    if runtime_provider not in {"codex-sdk", "codex-cli"}:
        raise EventError("event runtime_provider must be codex-sdk or codex-cli")
    run_id = text(payload.get("run_id"))
    try:
        priority = int(payload.get("priority", 0) or 0)
        max_retries = int(payload.get("max_retries", 2) or 0)
        repository_max_parallel_tasks = int(
            payload.get("repository_max_parallel_tasks", 0) or 0
        )
        batch_index = int(payload.get("batch_index", 0) or 0)
        graph_depth = int(payload.get("graph_depth", 0) or 0)
        attachment_count = int(payload.get("attachment_count", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise EventError("event numeric scheduling fields must be integers") from exc
    if priority < -100 or priority > 100 or max_retries < 0 or max_retries > 10:
        raise EventError("event priority or max_retries is outside allowed bounds")
    if repository_max_parallel_tasks < 0 or repository_max_parallel_tasks > 32:
        raise EventError("event repository_max_parallel_tasks must be between 0 and 32")
    if batch_index < 0 or graph_depth < 0 or graph_depth > 2:
        raise EventError("event batch_index or graph_depth is outside allowed bounds")
    if attachment_count < 0 or attachment_count > 5:
        raise EventError("event attachment_count must be between 0 and 5")
    attachment_runtime_consent = payload.get("attachment_runtime_consent", False)
    if not isinstance(attachment_runtime_consent, bool):
        raise EventError("event attachment_runtime_consent must be a boolean")
    input_manifest = text(payload.get("input_manifest"))
    input_manifest_sha256 = text(payload.get("input_manifest_sha256"))
    if bool(input_manifest) != bool(input_manifest_sha256):
        raise EventError("event attachment manifest metadata is incomplete")
    if input_manifest_sha256 and (
        len(input_manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in input_manifest_sha256)
    ):
        raise EventError("event input_manifest_sha256 must be a lowercase SHA-256 digest")
    if bool(attachment_count) != bool(input_manifest):
        raise EventError("event attachment_count must match attachment manifest presence")
    if attachment_count and not attachment_runtime_consent:
        raise EventError("event attachments require explicit runtime context consent")
    relation = text(payload.get("relation"), "root")
    if relation not in {"root", "repair", "investigation", "test", "implementation"}:
        raise EventError("event relation is invalid")
    dependency_mode = text(payload.get("dependency_mode"), "none")
    if dependency_mode not in {"none", "blocking", "non_blocking"}:
        raise EventError("event dependency_mode is invalid")
    allowed_paths = payload.get("allowed_paths", [])
    allowed_child_repositories = payload.get(
        "allowed_child_repositories", [str(repository)]
    )
    child_budget = payload.get("child_budget", {})
    if not isinstance(allowed_paths, list) or not all(isinstance(item, str) for item in allowed_paths):
        raise EventError("event allowed_paths must be a list of strings")
    if not isinstance(allowed_child_repositories, list) or not all(
        isinstance(item, str) for item in allowed_child_repositories
    ):
        raise EventError("event allowed_child_repositories must be a list of strings")
    if not isinstance(child_budget, dict):
        raise EventError("event child_budget must be an object")
    event_id = event_identity(source, external_id, task_id)
    envelope = {
        "event_id": event_id,
        "source": source,
        "event_type": text(payload.get("event_type") or payload.get("action"), "task"),
        "task_key": text(payload.get("task_key"), f"{source}:{external_id}:{task_id}"),
        "task_id": task_id,
        "goal": goal,
        "project": project,
        "repository": str(repository),
        "branch": branch,
        "base_branch": base_branch,
        "workspace_mode": workspace_mode,
        "checkout_path": text(payload.get("checkout_path"), str(repository)),
        "task_branch": text(payload.get("task_branch"), branch),
        "base_sha": text(payload.get("base_sha")),
        "branch_owner_run_id": text(payload.get("branch_owner_run_id"), run_id),
        "mode": mode,
        "runtime_provider": runtime_provider,
        "run_id": run_id,
        "priority": priority,
        "max_retries": max_retries,
        "repository_max_parallel_tasks": repository_max_parallel_tasks,
        "batch_id": text(payload.get("batch_id")),
        "batch_index": batch_index,
        "root_run_id": text(payload.get("root_run_id"), run_id),
        "parent_run_id": text(payload.get("parent_run_id")),
        "relation": relation,
        "dependency_mode": dependency_mode,
        "spawn_reason": text(payload.get("spawn_reason"))[:1000],
        "allowed_paths": allowed_paths[:50],
        "allowed_child_repositories": allowed_child_repositories[:20],
        "graph_depth": graph_depth,
        "child_budget": child_budget,
        "spawn_fingerprint": text(payload.get("spawn_fingerprint")),
        "external_id": external_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "github_repository": text(payload.get("repository", {}).get("full_name"))
            if isinstance(payload.get("repository"), dict)
            else "",
            "sender": text(payload.get("sender", {}).get("login"))
            if isinstance(payload.get("sender"), dict)
            else "",
        },
    }
    if input_manifest:
        envelope.update(
            {
                "input_manifest": input_manifest,
                "input_manifest_sha256": input_manifest_sha256,
                "attachment_count": attachment_count,
                "attachment_runtime_consent": attachment_runtime_consent,
            }
        )
    return envelope


def persist_event(envelope: dict[str, Any], directory: Path = EVENTS_DIR) -> Path:
    errors = validate_contract(envelope, load_json(TASK_SCHEMA), "task_envelope")
    if errors:
        raise EventError("invalid task envelope: " + "; ".join(errors))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{envelope['event_id']}.json"
    if not path.exists():
        path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def enqueue_envelope(queue: TaskQueue, envelope: dict[str, Any]) -> TaskRecord:
    persist_event(envelope, queue.path.parent / "events")
    payload = {
        key: envelope[key]
        for key in (
            "task_id", "goal", "project", "repository", "branch", "base_branch", "workspace_mode",
            "checkout_path", "task_branch", "base_sha", "branch_owner_run_id", "mode", "runtime_provider",
            "run_id", "source", "event_id", "repository_max_parallel_tasks", "batch_id", "batch_index",
            "root_run_id", "parent_run_id", "relation", "dependency_mode", "spawn_reason", "allowed_paths",
            "allowed_child_repositories", "graph_depth", "child_budget", "spawn_fingerprint"
        )
    }
    if "input_manifest" in envelope:
        payload.update(
            {
                key: envelope[key]
                for key in (
                    "input_manifest",
                    "input_manifest_sha256",
                    "attachment_count",
                    "attachment_runtime_consent",
                )
            }
        )
    return queue.enqueue(
        task_key=str(envelope["task_key"]),
        payload=payload,
        priority=int(envelope["priority"]),
        max_retries=int(envelope["max_retries"]),
        run_id=str(envelope["run_id"]),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(SOURCES), required=True)
    parser.add_argument("--payload", required=True, help="JSON object or @path")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--project", default="agent_workspace")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw = Path(args.payload[1:]).read_text(encoding="utf-8") if args.payload.startswith("@") else args.payload
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit("--payload must contain a JSON object")
    try:
        envelope = normalize_event(
            source=args.source,
            payload=payload,
            repository=args.repository,
            project=args.project,
        )
        record = enqueue_envelope(TaskQueue(args.db), envelope)
    except EventError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps({"envelope": envelope, "queue_task": record.__dict__}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
