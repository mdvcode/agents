from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from list_runs import collect
from task_queue import TaskQueue


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_requires_human_lists_only_exceptions(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    write_json(runs / "completed" / "workflow.json", {"execution_status": "completed"})
    write_json(
        runs / "approval" / "workflow.json",
        {"execution_status": "awaiting_approval", "blockers": ["same failure repeated"]},
    )
    db = tmp_path / "queue.db"
    queue = TaskQueue(db)
    task = queue.enqueue(task_key="dead", payload={"task_id": "x", "repository": str(tmp_path)}, max_retries=0)
    queue.claim(worker_id="worker")
    queue.mark_running(task.id, "worker")
    queue.finish(task_id=task.id, worker_id="worker", status="failed", error="boom")

    entries = collect(runs_dir=runs, db_path=db, requires_human=True)

    assert {(entry.source, entry.status) for entry in entries} == {
        ("run", "awaiting_approval"),
        ("queue", "dead_letter"),
    }


def test_status_and_stalled_filters(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    workflow = runs / "stalled-run" / "workflow.json"
    write_json(workflow, {"execution_status": "running"})
    old = time.time() - 600
    os.utime(workflow, (old, old))

    stalled = collect(
        runs_dir=runs,
        db_path=tmp_path / "missing.db",
        stalled=True,
        stale_seconds=60,
    )
    running = collect(
        runs_dir=runs,
        db_path=tmp_path / "missing.db",
        status="running",
        stale_seconds=60,
    )

    assert [entry.identifier for entry in stalled] == ["stalled-run"]
    assert [entry.identifier for entry in running] == ["stalled-run"]


def test_sdk_event_progress_is_authoritative_for_stuck_detection(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    run = runs / "active-sdk"
    workflow = run / "workflow.json"
    write_json(workflow, {"execution_status": "running"})
    old = time.time() - 600
    os.utime(workflow, (old, old))
    write_json(
        run / "progress.json",
        {"last_sdk_event": "item/started", "active_tool": "pytest -q"},
    )

    entries = collect(
        runs_dir=runs,
        db_path=tmp_path / "missing.db",
        stalled=True,
        stale_seconds=60,
    )

    assert entries == []


def test_security_and_draft_evidence_exceptions_are_summarized(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    run = runs / "draft"
    write_json(run / "workflow.json", {"execution_status": "completed"})
    write_json(run / "artifacts" / "security.json", {"verdict": "broken", "status": "fail"})
    write_json(run / "artifacts" / "publication.json", {"pr_state": "draft"})
    write_json(
        run / "artifacts" / "verdict.json",
        {"visual_evidence": {"required": True, "provided": False}},
    )

    entries = collect(
        runs_dir=runs,
        db_path=tmp_path / "missing.db",
        requires_human=True,
    )

    assert len(entries) == 1
    assert "security gate failed" in entries[0].reasons
    assert "PR draft requires evidence" in entries[0].reasons
