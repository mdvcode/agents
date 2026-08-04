from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import worker_pool  # noqa: E402
from task_queue import TaskQueue  # noqa: E402
from worker_pool import WorkerOutcome, WorkflowWorkerPool  # noqa: E402


def test_one_worker_future_crash_does_not_stop_other_workers(tmp_path: Path, monkeypatch: object) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue(task_key="ok", payload={"task_id": "ok", "repository": str(tmp_path)})
    pool = WorkflowWorkerPool(queue=queue, workers=2, handler=lambda record, _worker: WorkerOutcome("completed", f"run-{record.id}"))
    original = pool.process_one

    def injected(number: int):
        if number == 1:
            raise KeyError("thread crash")
        return original(number)

    monkeypatch.setattr(pool, "process_one", injected)
    records = pool.run_wave()
    assert any(record.status == "completed" for record in records)
    assert (tmp_path / "worker-pool-errors.jsonl").is_file()


def test_telemetry_failure_is_fail_open(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
    monkeypatch.setattr(worker_pool, "TelemetryRuntime", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("telemetry down")))
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue(task_key="ok", payload={"task_id": "ok", "repository": str(tmp_path)})
    pool = WorkflowWorkerPool(queue=queue, workers=1, handler=lambda _record, _worker: WorkerOutcome("completed", "run-ok"))
    assert pool.run_wave()[0].status == "completed"


def test_unknown_worker_exception_is_persisted_and_serviceable(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue(task_key="bad", payload={"task_id": "bad", "repository": str(tmp_path)}, run_id="run-bad")
    pool = WorkflowWorkerPool(queue=queue, workers=1, handler=lambda _record, _worker: (_ for _ in ()).throw(KeyError("unknown field")))
    record = pool.run_wave()[0]
    assert record.status == "resuming"
    failures = list((tmp_path / ".agent-runs" / "run-bad" / "failures").glob("*.json"))
    assert failures
    error = json.loads((tmp_path / ".agent-runs" / "run-bad" / "errors.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert error["failure_kind"] == "internal_error"
