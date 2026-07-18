from __future__ import annotations

import threading
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue, TaskRecord
from worker_pool import WorkerOutcome, WorkflowWorkerPool, safe_payload


def test_three_workers_process_isolated_tasks_concurrently(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    for index in range(3):
        queue.enqueue(
            task_key=f"task-{index}",
            payload={"task_id": f"task-{index}", "repository": str(tmp_path)},
        )
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def handler(record: TaskRecord, worker_id: str) -> WorkerOutcome:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        worktree = tmp_path / "worktrees" / f"task-{record.id}"
        worktree.mkdir(parents=True)
        time.sleep(0.05)
        with lock:
            active -= 1
        return WorkerOutcome(status="completed", run_id=f"run-{record.id}")

    pool = WorkflowWorkerPool(
        queue=queue,
        workers=3,
        lease_seconds=30,
        heartbeat_seconds=1,
        handler=handler,
    )
    records = pool.drain()

    assert maximum_active >= 2
    assert len([record for record in records if record.status == "completed"]) == 3
    assert len(list((tmp_path / "worktrees").iterdir())) == 3
    leased_workers = {event["worker_id"] for event in queue.events() if event["event"] == "leased"}
    assert len(leased_workers) == 3


def test_worker_pool_retries_to_dead_letter(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue(
        task_key="fails",
        payload={"task_id": "fails", "repository": str(tmp_path)},
        max_retries=1,
    )

    pool = WorkflowWorkerPool(
        queue=queue,
        workers=1,
        lease_seconds=30,
        heartbeat_seconds=1,
        handler=lambda _record, _worker: WorkerOutcome(status="failed", error="boom"),
    )
    pool.drain()

    record = queue.list()[0]
    assert record.status == "dead_letter"
    assert record.requires_human is True


def test_worker_rejects_adapter_override_outside_harness_test_mode(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.delenv("AGENT_HARNESS_TEST_MODE", raising=False)
    queue = TaskQueue(tmp_path / "queue.db")
    record = queue.enqueue(
        task_key="unsafe-adapter",
        payload={
            "task_id": "unsafe-adapter",
            "repository": str(tmp_path),
            "adapter_command": "python fake.py",
        },
    )

    try:
        safe_payload(record)
    except ValueError as exc:
        assert "restricted to harness test mode" in str(exc)
    else:
        raise AssertionError("adapter override must be rejected")
