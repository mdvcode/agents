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
    run_id = text(payload.get("run_id"))
    try:
        priority = int(payload.get("priority", 0) or 0)
        max_retries = int(payload.get("max_retries", 2) or 0)
    except (TypeError, ValueError) as exc:
        raise EventError("event priority and max_retries must be integers") from exc
    if priority < -100 or priority > 100 or max_retries < 0 or max_retries > 10:
        raise EventError("event priority or max_retries is outside allowed bounds")
    event_id = event_identity(source, external_id, task_id)
    return {
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
        "run_id": run_id,
        "priority": priority,
        "max_retries": max_retries,
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
            "task_id", "goal", "project", "repository", "branch", "base_branch", "run_id", "source", "event_id"
        )
    }
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
