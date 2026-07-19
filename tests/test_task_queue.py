from __future__ import annotations

import time
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue


def test_enqueue_is_idempotent_and_claims_are_unique(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    first = queue.enqueue(task_key="same", payload={"task_id": "a", "repository": "/tmp/repo"})
    duplicate = queue.enqueue(task_key="same", payload={"task_id": "different", "repository": "/tmp/repo"})
    assert duplicate.id == first.id
    for index in range(1, 4):
        queue.enqueue(task_key=f"task-{index}", payload={"task_id": str(index), "repository": "/tmp/repo"})

    with ThreadPoolExecutor(max_workers=3) as executor:
        claimed = list(executor.map(lambda index: queue.claim(worker_id=f"worker-{index}"), range(3)))

    ids = [record.id for record in claimed if record is not None]
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_heartbeat_extends_only_owned_active_lease(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.enqueue(task_key="heartbeat", payload={"task_id": "a", "repository": "/tmp/repo"})
    claimed = queue.claim(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None
    before = claimed.lease_expires_at
    assert queue.mark_running(task.id, "worker-1") is True
    assert queue.heartbeat(task.id, "worker-2", 60) is False
    time.sleep(0.01)
    assert queue.heartbeat(task.id, "worker-1", 60) is True
    assert queue.get(task.id).lease_expires_at > before  # type: ignore[union-attr]


def test_failures_retry_then_enter_dead_letter(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.enqueue(
        task_key="retry",
        payload={"task_id": "a", "repository": "/tmp/repo"},
        max_retries=1,
    )
    first = queue.claim(worker_id="worker-1")
    assert first is not None
    queue.mark_running(task.id, "worker-1")
    retried = queue.finish(
        task_id=task.id,
        worker_id="worker-1",
        status="failed",
        error="first failure",
    )
    assert retried.status == "queued"

    second = queue.claim(worker_id="worker-2")
    assert second is not None
    queue.mark_running(task.id, "worker-2")
    dead = queue.finish(
        task_id=task.id,
        worker_id="worker-2",
        status="failed",
        error="same failure",
    )
    assert dead.status == "dead_letter"
    assert dead.requires_human is True
    assert dead.attempts == 2


def test_expired_lease_is_reclaimed(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.enqueue(task_key="expired", payload={"task_id": "a", "repository": "/tmp/repo"})
    claimed = queue.claim(worker_id="stalled", lease_seconds=30)
    assert claimed is not None
    with queue.connect() as connection:
        connection.execute("UPDATE tasks SET lease_expires_at=0 WHERE id=?", (task.id,))

    reclaimed = queue.claim(worker_id="replacement", lease_seconds=30)

    assert reclaimed is not None
    assert reclaimed.id == task.id
    assert reclaimed.worker_id == "replacement"
    assert reclaimed.attempts == 2


def test_expired_lease_preserves_run_for_checkpoint_resume(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.enqueue(
        task_key="recover",
        payload={"task_id": "recover", "repository": str(tmp_path), "run_id": "run-recover"},
        run_id="run-recover",
    )
    claimed = queue.claim(worker_id="worker-dead", lease_seconds=30)
    assert claimed is not None
    assert queue.mark_running(task.id, "worker-dead")
    with queue.connect() as connection:
        connection.execute("UPDATE tasks SET lease_expires_at=0 WHERE id=?", (task.id,))

    recovered = queue.claim(worker_id="worker-new", lease_seconds=30)

    assert recovered is not None
    assert recovered.run_id == "run-recover"
    assert any(event["event"] == "lease_expired_resume" for event in queue.events(task.id))


def test_worker_registration_heartbeat_and_stale_monitor(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    registered = queue.register_worker(worker_id="svc-1", service_id="svc", pid=123)
    assert registered.status == "starting"
    assert queue.worker_heartbeat("svc-1", current_task_id=42)
    with queue.connect() as connection:
        connection.execute("UPDATE workers SET heartbeat_at=0 WHERE worker_id='svc-1'")

    stale = queue.stale_workers(stale_seconds=1, mark=True)

    assert [record.worker_id for record in stale] == ["svc-1"]
    assert stale[0].status == "stalled"
    assert queue.stop_worker("svc-1")
