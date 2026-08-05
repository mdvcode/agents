from __future__ import annotations

import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue
from worker_service import WorkerService
from worker_pool import WorkerOutcome
import worker_pool


class EmptyPool:
    def run_wave(self) -> list[object]:
        return []


def test_worker_service_registers_reports_health_and_stops_gracefully(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    state_path = tmp_path / "service.json"
    service = WorkerService(
        queue=queue,
        service_id="service-test",
        workers=2,
        lease_seconds=30,
        heartbeat_seconds=1,
        poll_seconds=0.01,
        state_path=state_path,
    )
    service.pool = EmptyPool()  # type: ignore[assignment]
    service.register()

    health = service.health()
    result = service.serve(once=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    workers = [record for record in queue.list_workers() if record.service_id == "service-test"]

    assert health["status"] == "healthy"
    assert len(health["workers"]) == 2
    assert result == 0
    assert state["status"] == "stopped"
    assert {record.status for record in workers} == {"stopped"}


def test_service_restart_reclaims_active_task_and_continues_same_run(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
    queue = TaskQueue(tmp_path / "queue.db")
    queued = queue.enqueue(
        task_key="restart-active",
        payload={"task_id": "restart-active", "repository": str(tmp_path), "run_id": "run-restart"},
        run_id="run-restart",
    )
    old_service = WorkerService(
        queue=queue,
        service_id="restart-service",
        workers=1,
        lease_seconds=30,
        heartbeat_seconds=1,
        poll_seconds=0.01,
        state_path=tmp_path / "old-service.json",
    )
    old_service.register()
    claimed = queue.claim(worker_id="restart-service-1", lease_seconds=30)
    assert claimed is not None
    assert queue.mark_running(queued.id, "restart-service-1")
    assert queue.assign_run(queued.id, "restart-service-1", "run-restart")
    with queue.connect() as connection:
        connection.execute("UPDATE tasks SET lease_expires_at=? WHERE id=?", (time.time() - 1, queued.id))

    replacement = WorkerService(
        queue=queue,
        service_id="restart-service",
        workers=1,
        lease_seconds=30,
        heartbeat_seconds=1,
        poll_seconds=0.01,
        state_path=tmp_path / "replacement-service.json",
        restart_count=1,
    )
    replacement.pool.handler = lambda record, _worker: WorkerOutcome(status="completed", run_id=record.run_id)
    replacement.register()

    records = replacement.pool.run_wave()

    assert [record.status for record in records] == ["completed"]
    assert records[0].run_id == "run-restart"
    assert replacement.total_restart_count == 1
    assert any(event["event"] == "lease_expired_resume" for event in queue.events(queued.id))
