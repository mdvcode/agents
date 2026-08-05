from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from event_ingestion import EventError, enqueue_envelope, normalize_event
from task_queue import TaskQueue


@pytest.mark.parametrize("source", ["cli", "github_issue", "webhook", "api", "ci"])
def test_all_sources_normalize_to_one_task_envelope(tmp_path: Path, source: str) -> None:
    payload = {
        "external_id": f"external-{source}",
        "task_id": f"task-{source}",
        "goal": f"Handle {source}",
    }
    envelope = normalize_event(source=source, payload=payload, repository=tmp_path)

    assert envelope["source"] == source
    assert envelope["task_id"] == f"task-{source}"
    assert envelope["repository"] == str(tmp_path.resolve())
    assert envelope["workspace_mode"] == "isolated"
    assert envelope["event_id"]


def test_event_delivery_is_idempotent_in_queue(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    envelope = normalize_event(
        source="github_issue",
        payload={"issue": {"id": 10, "number": 4, "title": "Fix it", "body": "Details"}},
        repository=tmp_path,
    )

    first = enqueue_envelope(queue, envelope)
    second = enqueue_envelope(queue, envelope)

    assert first.id == second.id
    assert first.payload["goal"] == "Fix it\n\nDetails"
    assert len(list((tmp_path / "events").glob("*.json"))) == 1


def test_event_rejects_non_numeric_queue_controls(tmp_path: Path) -> None:
    with pytest.raises(EventError, match="must be integers"):
        normalize_event(
            source="api",
            payload={"external_id": "bad", "priority": "urgent"},
            repository=tmp_path,
        )


def test_current_branch_workspace_mode_survives_normalization_and_queueing(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    envelope = normalize_event(
        source="cli",
        payload={
            "external_id": "current-1",
            "task_id": "current-1",
            "goal": "Use the checkout",
            "branch": "feature/current",
            "workspace_mode": "current_branch",
        },
        repository=tmp_path,
    )

    record = enqueue_envelope(queue, envelope)

    assert envelope["workspace_mode"] == "current_branch"
    assert record.payload["workspace_mode"] == "current_branch"
