from __future__ import annotations

import threading
import time
import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue, TaskRecord
from worker_pool import (
    WorkerOutcome,
    WorkflowWorkerPool,
    safe_payload,
    telemetry_run_dir,
    workflow_attention_reason,
)
import worker_pool


def test_three_workers_process_isolated_tasks_concurrently(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
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


def test_workers_serialize_current_branch_tasks_in_the_same_checkout(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
    queue = TaskQueue(tmp_path / "queue.db")
    for index in range(2):
        queue.enqueue(
            task_key=f"current-{index}",
            payload={
                "task_id": f"current-{index}",
                "repository": str(tmp_path / "checkout"),
                "workspace_mode": "checkout",
            },
        )
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def handler(record: TaskRecord, _worker_id: str) -> WorkerOutcome:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return WorkerOutcome(status="completed", run_id=f"run-{record.id}")

    records = WorkflowWorkerPool(
        queue=queue,
        workers=2,
        lease_seconds=30,
        heartbeat_seconds=1,
        handler=handler,
    ).drain()

    assert maximum_active == 1
    assert [record.status for record in records] == ["completed", "completed"]


def test_worker_pool_retries_to_dead_letter(tmp_path: Path, monkeypatch: object) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue(
        task_key="fails",
        payload={"task_id": "fails", "repository": str(tmp_path)},
        max_retries=1,
    )

    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
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
    spans_path = tmp_path / ".agent-runs" / f"queue-task-{record.id}" / "raw-events" / "otel-spans.jsonl"
    spans = [json.loads(line) for line in spans_path.read_text(encoding="utf-8").splitlines()]
    assert spans[-1]["name"] == "ai_harness.worker.task"
    assert spans[-1]["status"] == "error"


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


def test_worker_rejects_unknown_workspace_mode(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    record = queue.enqueue(
        task_key="unsafe-workspace",
        payload={"task_id": "unsafe-workspace", "repository": str(tmp_path), "workspace_mode": "shared"},
    )

    try:
        safe_payload(record)
    except ValueError as exc:
        assert "workspace_mode must be checkout or worktree" in str(exc)
    else:
        raise AssertionError("unknown workspace modes must be rejected")


def test_worker_rejects_unknown_execution_mode(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    record = queue.enqueue(
        task_key="unsafe-mode",
        payload={"task_id": "unsafe-mode", "repository": str(tmp_path), "mode": "turbo"},
    )

    with pytest.raises(ValueError, match="mode must be auto, fast, full, or goal"):
        safe_payload(record)


def test_worker_accepts_explicit_goal_mode(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    record = queue.enqueue(
        task_key="long-goal",
        payload={"task_id": "long-goal", "repository": str(tmp_path), "mode": "goal"},
    )

    assert safe_payload(record)["mode"] == "goal"


def test_worker_preserves_exact_attention_question() -> None:
    workflow = {
        "execution_status": "awaiting_approval",
        "attention": {
            "required": True,
            "summary": "Which region should be used?",
            "details": ["Choose eu-west-1 or eu-central-1."],
            "role": "planner",
            "action": "answer_or_approve",
        },
    }

    reason = workflow_attention_reason(workflow, "approval required")

    assert reason == "Which region should be used?; Choose eu-west-1 or eu-central-1."


def test_worker_telemetry_rejects_run_paths_outside_run_store(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")

    assert telemetry_run_dir("safe-run") == (tmp_path / ".agent-runs" / "safe-run").resolve()
    assert telemetry_run_dir("../outside") is None


def test_reclaimed_run_uses_resume_command(tmp_path: Path, monkeypatch: object) -> None:
    runs = tmp_path / ".agent-runs"
    run_dir = runs / "run-recover"
    run_dir.mkdir(parents=True)
    (run_dir / "workflow.json").write_text(
        json.dumps({"execution_status": "running"}), encoding="utf-8"
    )
    monkeypatch.setattr(worker_pool, "RUNS_DIR", runs)
    commands: list[list[str]] = []

    class FakeProcess:
        returncode = 1

        def __init__(self, command: list[str], **_kwargs: object) -> None:
            commands.append(command)

        def poll(self) -> int:
            return 1

        def communicate(self, timeout: int | None = None) -> tuple[str, str]:
            return "", "interrupted"

    monkeypatch.setattr(worker_pool.subprocess, "Popen", FakeProcess)
    queue = TaskQueue(tmp_path / "queue.db")
    queued = queue.enqueue(
        task_key="recover",
        payload={
            "task_id": "recover",
            "repository": str(tmp_path),
            "run_id": "run-recover",
            "workspace_mode": "current_branch",
        },
        run_id="run-recover",
    )
    claimed = queue.claim(worker_id="replacement", lease_seconds=30)
    assert claimed is not None
    assert queue.mark_running(queued.id, "replacement")
    pool = WorkflowWorkerPool(queue=queue, workers=1, lease_seconds=30, heartbeat_seconds=1)

    outcome = pool.run_workflow(claimed, "replacement")

    assert outcome.run_id == "run-recover"
    assert commands and "--resume" in commands[0]
    assert "--current-branch" in commands[0]


def test_two_workers_cannot_resume_the_same_run_concurrently(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
    queue = TaskQueue(tmp_path / "queue.db")
    queued = queue.enqueue(
        task_key="single-resume",
        payload={"task_id": "single-resume", "repository": str(tmp_path), "run_id": "run-single"},
        run_id="run-single",
    )
    first = queue.claim(worker_id="initial", lease_seconds=30)
    assert first is not None
    assert queue.mark_running(queued.id, "initial")
    queue.mark_resuming(
        task_id=queued.id,
        worker_id="initial",
        run_id="run-single",
        available_after=time.time(),
        recovery_action="resume",
        resume_checkpoint="before_runtime_execute",
    )
    calls: list[str] = []
    lock = threading.Lock()

    def handler(record: TaskRecord, worker_id: str) -> WorkerOutcome:
        with lock:
            calls.append(worker_id)
        time.sleep(0.05)
        return WorkerOutcome(status="completed", run_id=record.run_id)

    pool = WorkflowWorkerPool(
        queue=queue,
        workers=2,
        lease_seconds=30,
        heartbeat_seconds=1,
        handler=handler,
    )

    records = pool.run_wave()

    assert len(calls) == 1
    assert [record.status for record in records] == ["completed"]
    assert queue.get(queued.id).run_id == "run-single"  # type: ignore[union-attr]
