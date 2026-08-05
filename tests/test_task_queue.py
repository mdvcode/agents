from __future__ import annotations

import time
import sys
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import task_queue
from task_queue import TaskQueue


class RecordingInstrument:
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, str]]] = []

    def add(self, value: int, attributes: dict[str, str]) -> None:
        self.calls.append((value, attributes))


class RecordingTelemetry:
    def __init__(self) -> None:
        self.queue_lease_expirations_total = RecordingInstrument()
        self.dead_letters_total = RecordingInstrument()

    def shutdown(self) -> None:
        return


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


def test_claim_serializes_current_branch_tasks_for_one_checkout(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    repository = str((tmp_path / "shared").resolve())
    first = queue.enqueue(
        task_key="current-1",
        payload={"task_id": "current-1", "repository": repository, "workspace_mode": "current_branch"},
    )
    second = queue.enqueue(
        task_key="current-2",
        payload={"task_id": "current-2", "repository": repository, "workspace_mode": "current_branch"},
    )
    unrelated = queue.enqueue(
        task_key="current-other",
        payload={
            "task_id": "current-other",
            "repository": str((tmp_path / "other").resolve()),
            "workspace_mode": "current_branch",
        },
    )

    claimed_first = queue.claim(worker_id="worker-1")
    claimed_other = queue.claim(worker_id="worker-2")
    blocked_same_checkout = queue.claim(worker_id="worker-3")

    assert claimed_first is not None and claimed_first.id == first.id
    assert claimed_other is not None and claimed_other.id == unrelated.id
    assert blocked_same_checkout is None
    queue.finish(task_id=first.id, worker_id="worker-1", status="completed")
    claimed_second = queue.claim(worker_id="worker-3")
    assert claimed_second is not None and claimed_second.id == second.id


def test_unfinished_current_branch_task_keeps_checkout_reserved(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    repository = str((tmp_path / "shared").resolve())
    first = queue.enqueue(
        task_key="approval-1",
        payload={
            "task_id": "approval-1",
            "repository": repository,
            "workspace_mode": "current_branch",
            "run_id": "run-approval",
        },
        run_id="run-approval",
    )
    queue.enqueue(
        task_key="approval-2",
        payload={"task_id": "approval-2", "repository": repository, "workspace_mode": "current_branch"},
    )
    claimed = queue.claim(worker_id="worker-1")
    assert claimed is not None and claimed.id == first.id
    queue.finish(
        task_id=first.id,
        worker_id="worker-1",
        status="awaiting_approval",
        requires_human=True,
        exception_reason="approval required",
    )

    assert queue.claim(worker_id="worker-2") is None
    queue.abort_run("run-approval")
    released = queue.claim(worker_id="worker-2")
    assert released is not None and released.payload["task_id"] == "approval-2"


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
    assert retried.status == "retry_wait"
    assert retried.recovery_action == "retry"

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


def test_expired_lease_preserves_run_for_checkpoint_resume(tmp_path: Path, monkeypatch: object) -> None:
    telemetry = RecordingTelemetry()
    monkeypatch.setattr(task_queue, "safe_telemetry_runtime", lambda **_kwargs: telemetry)
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
    assert recovered.lease_owner == "worker-new"
    assert any(event["event"] == "lease_expired_resume" for event in queue.events(task.id))
    assert telemetry.queue_lease_expirations_total.calls == [(1, {"outcome": "resuming"})]


def test_public_reclaim_expired_leases_method_returns_recovered_records(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.enqueue(
        task_key="public-reclaim",
        payload={"task_id": "public-reclaim", "repository": str(tmp_path)},
    )
    claimed = queue.claim(worker_id="stalled", lease_seconds=30)
    assert claimed is not None
    assert claimed.lease_owner == "stalled"
    assert queue.mark_running(task.id, "stalled")
    with queue.connect() as connection:
        connection.execute("UPDATE tasks SET lease_expires_at=0 WHERE id=?", (task.id,))

    recovered = queue.reclaim_expired_leases(now=time.time())

    assert [record.id for record in recovered] == [task.id]
    assert recovered[0].status == "retry_wait"
    assert recovered[0].lease_owner == ""


def test_lease_owner_migration_preserves_existing_recovery_state(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy_sql = task_queue.TASKS_TABLE_SQL.replace("    lease_owner TEXT NOT NULL DEFAULT '',\n", "")
    with sqlite3.connect(path) as connection:
        connection.execute(legacy_sql.format(table="tasks"))
        connection.execute(
            """
            INSERT INTO tasks(
                task_key,payload_json,status,run_id,worker_id,lease_expires_at,heartbeat_at,
                available_at,created_at,updated_at,failure_kind,recovery_action,
                resume_checkpoint,recovery_attempts,last_failure_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy",
                '{"task_id":"legacy","repository":"/tmp/repo"}',
                "running",
                "run-legacy",
                "worker-old",
                time.time() + 30,
                time.time(),
                time.time(),
                time.time(),
                time.time(),
                "transient",
                "resume",
                "before_runtime_execute",
                2,
                "failure-2",
            ),
        )

    queue = TaskQueue(path)
    record = queue.list()[0]

    assert record.lease_owner == "worker-old"
    assert record.failure_kind == "transient"
    assert record.recovery_action == "resume"
    assert record.resume_checkpoint == "before_runtime_execute"
    assert record.recovery_attempts == 2
    assert record.last_failure_id == "failure-2"


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
