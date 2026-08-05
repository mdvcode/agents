from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import worker_pool  # noqa: E402
from task_queue import TaskQueue  # noqa: E402
from worker_pool import WorkerOutcome, WorkflowWorkerPool, persist_workflow_cancellation  # noqa: E402


class RecordingInstrument:
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, str]]] = []

    def add(self, value: int, attributes: dict[str, str]) -> None:
        self.calls.append((value, attributes))


class RecordingSpan:
    def set_status(self, _status: object) -> None:
        return


class RecordingTelemetry:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.worker_crashes_total = RecordingInstrument()

    @contextmanager
    def span(self, name: str, _attributes: object = None) -> Iterator[RecordingSpan]:
        self.names.append(name)
        yield RecordingSpan()

    def shutdown(self) -> None:
        return


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
    monkeypatch.setattr(worker_pool, "safe_telemetry_runtime", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("telemetry down")))
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


def test_graceful_service_interruption_requeues_the_same_run(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue(
        task_key="shutdown",
        payload={"task_id": "shutdown", "repository": str(tmp_path), "run_id": "run-shutdown"},
        run_id="run-shutdown",
    )
    claimed = queue.claim(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None
    assert queue.mark_running(claimed.id, "worker-1")
    pool = WorkflowWorkerPool(queue=queue, workers=1, lease_seconds=30, heartbeat_seconds=1)

    outcome = pool._recovery_outcome(
        claimed,
        RuntimeError("workflow interrupted by graceful worker shutdown"),
        run_id="run-shutdown",
        process_returncode=75,
    )
    recovered = pool._finish_outcome(claimed, "worker-1", outcome)

    assert recovered.status in {"retry_wait", "resuming"}
    assert recovered.run_id == "run-shutdown"
    assert recovered.resume_checkpoint


def test_worker_crash_emits_required_fail_open_span_and_counter(tmp_path: Path, monkeypatch: object) -> None:
    recording = RecordingTelemetry()
    monkeypatch.setattr(worker_pool, "safe_telemetry_runtime", lambda **_kwargs: recording)
    queue = TaskQueue(tmp_path / "queue.db")
    pool = WorkflowWorkerPool(queue=queue, workers=1)

    pool.record_pool_failure(1, KeyError("crash"), run_id="run-crash")

    assert recording.names == ["ai_harness.worker.crash"]
    assert recording.worker_crashes_total.calls == [(1, {"error.type": "KeyError"})]


def test_user_cancellation_persists_terminal_workflow_state_and_checkpoint(tmp_path: Path) -> None:
    workflow_path = tmp_path / "run-cancel" / "workflow.json"
    workflow_path.parent.mkdir()
    workflow_path.write_text(
        json.dumps(
            {
                "run_id": "run-cancel",
                "execution_status": "role_running",
                "current_role": "implementation-agent",
                "resume_from": "before_runtime_execute",
            }
        ),
        encoding="utf-8",
    )

    persist_workflow_cancellation(workflow_path)

    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert workflow["execution_status"] == "cancelled"
    assert workflow["cancellation_checkpoint"] == "before_runtime_execute"
    assert workflow["cancellation_requested_at"]
