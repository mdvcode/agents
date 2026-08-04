from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue  # noqa: E402


def test_retry_preserves_run_worktree_and_attempt_state(tmp_path: Path) -> None:
    worktree = tmp_path / "task-worktree"
    worktree.mkdir()
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.enqueue(
        task_key="same-run",
        payload={"task_id": "task-1", "repository": str(tmp_path), "worktree": str(worktree)},
        run_id="run-1",
    )
    claimed = queue.claim(worker_id="worker-1")
    assert claimed is not None
    assert queue.mark_running(task.id, "worker-1")
    retried = queue.schedule_retry(
        task_id=task.id,
        worker_id="worker-1",
        run_id="run-1",
        available_after=time.time(),
        preserve_attempt_state=True,
        failure_kind="transient",
        recovery_action="retry",
        resume_checkpoint="before_runtime_execute",
        failure_id="failure-1",
        error="timeout",
    )
    reclaimed = queue.claim(worker_id="worker-2")
    assert retried.status == "retry_wait"
    assert reclaimed is not None
    assert reclaimed.run_id == "run-1"
    assert reclaimed.payload["worktree"] == str(worktree)
    assert reclaimed.recovery_attempts == 1


def test_explicit_recovery_states_and_dead_letter_listing(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.enqueue(task_key="repair", payload={"task_id": "task-1", "repository": str(tmp_path)}, run_id="run-1")
    claimed = queue.claim(worker_id="worker-1")
    assert claimed is not None
    queue.mark_running(task.id, "worker-1")
    repairing = queue.mark_repairing(
        task_id=task.id,
        worker_id="worker-1",
        run_id="run-1",
        available_after=time.time(),
        failure_kind="invalid_output",
        recovery_action="repair",
        resume_checkpoint="role_validating",
        failure_id="failure-1",
        error="schema",
    )
    assert repairing in queue.list_recoverable()
    claimed = queue.claim(worker_id="worker-2")
    assert claimed is not None
    queue.mark_running(task.id, "worker-2")
    dead = queue.move_to_dead_letter(
        task_id=task.id,
        worker_id="worker-2",
        run_id="run-1",
        error="budget exhausted",
        failure_kind="invalid_output",
        failure_id="failure-2",
    )
    assert dead.status == "dead_letter"
    assert queue.list(status="dead_letter") == [dead]


def test_manual_recovery_cannot_bypass_approval_or_reopen_terminal_task(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    queued = queue.enqueue(
        task_key="approval-task",
        payload={"task_id": "approval-task", "repository": str(tmp_path)},
        run_id="approval-run",
    )
    claimed = queue.claim(worker_id="worker-1")
    assert claimed is not None and claimed.id == queued.id
    assert queue.mark_running(claimed.id, "worker-1")
    gated = queue.finish(
        task_id=claimed.id,
        worker_id="worker-1",
        status="awaiting_approval",
        run_id="approval-run",
        requires_human=True,
    )
    assert gated.status == "awaiting_approval"

    with pytest.raises(ValueError, match="approval lifecycle"):
        queue.recover_run("approval-run", action="retry")

    completed = queue.enqueue(
        task_key="completed-task",
        payload={"task_id": "completed-task", "repository": str(tmp_path)},
        run_id="completed-run",
    )
    claimed_completed = queue.claim(worker_id="worker-2")
    assert claimed_completed is not None and claimed_completed.id == completed.id
    assert queue.mark_running(claimed_completed.id, "worker-2")
    queue.finish(
        task_id=claimed_completed.id,
        worker_id="worker-2",
        status="completed",
        run_id="completed-run",
    )

    with pytest.raises(ValueError, match="terminal completed"):
        queue.recover_run("completed-run", action="resume")
    with pytest.raises(ValueError, match="terminal completed"):
        queue.abort_run("completed-run")
