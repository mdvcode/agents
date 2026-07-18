from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue
from worker_service import WorkerService


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
