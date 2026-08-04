from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue  # noqa: E402
from worker_service import WorkerService  # noqa: E402


class RecoveringPool:
    def __init__(self, service: WorkerService) -> None:
        self.service = service
        self.calls = 0

    def run_wave(self) -> list[object]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("system wave failure")
        self.service.stop_event.set()
        return []


class TaskFailurePool:
    def __init__(self, service: WorkerService) -> None:
        self.service = service
        self.calls = 0

    def run_wave(self) -> list[object]:
        self.calls += 1
        if self.calls >= 5:
            self.service.stop_event.set()
        return [object()]


def service(tmp_path: Path) -> WorkerService:
    return WorkerService(
        queue=TaskQueue(tmp_path / "queue.db"),
        service_id="self-healing",
        workers=1,
        lease_seconds=30,
        heartbeat_seconds=1,
        poll_seconds=0.001,
        state_path=tmp_path / "service.json",
    )


def test_successful_wave_resets_consecutive_failure_count(tmp_path: Path) -> None:
    instance = service(tmp_path)
    instance.pool = RecoveringPool(instance)  # type: ignore[assignment]
    assert instance.serve() == 1
    assert instance.total_restart_count == 1
    assert instance.consecutive_failure_count == 0
    state = json.loads((tmp_path / "service.json").read_text(encoding="utf-8"))
    assert state["status"] == "stopped"


def test_five_unrelated_task_failures_do_not_restart_service(tmp_path: Path) -> None:
    instance = service(tmp_path)
    instance.pool = TaskFailurePool(instance)  # type: ignore[assignment]
    assert instance.serve() == 0
    assert instance.total_restart_count == 0
    assert instance.consecutive_failure_count == 0
