#!/usr/bin/env python3
"""SQLite-backed task queue with leases, heartbeats, retries, and dead letters."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.observability import safe_telemetry_runtime
from ai_harness.recovery.models import FailureRecord, persist_failure

DEFAULT_DB = ROOT / ".agent-queue" / "tasks.db"
ACTIVE_STATUSES = {"claimed", "leased", "running"}
RECOVERABLE_STATUSES = {"queued", "retry_wait", "repairing", "resuming"}
FINAL_STATUSES = {"completed", "blocked", "dead_letter", "failed", "cancelled"}
ALL_STATUSES = {*RECOVERABLE_STATUSES, *ACTIVE_STATUSES, "awaiting_approval", *FINAL_STATUSES}
SQLITE_BUSY_BACKOFF_SECONDS = (0.05, 0.1, 0.25)

TASKS_TABLE_SQL = """
CREATE TABLE {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'queued','claimed','leased','running','retry_wait','repairing','resuming',
        'awaiting_approval','completed','blocked','dead_letter','failed','cancelled'
    )),
    priority INTEGER NOT NULL DEFAULT 0,
    run_id TEXT NOT NULL DEFAULT '',
    worker_id TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_expires_at REAL NOT NULL DEFAULT 0,
    heartbeat_at REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 2,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    requires_human INTEGER NOT NULL DEFAULT 0,
    exception_reason TEXT NOT NULL DEFAULT '',
    failure_kind TEXT NOT NULL DEFAULT '',
    recovery_action TEXT NOT NULL DEFAULT '',
    next_attempt_at REAL NOT NULL DEFAULT 0,
    resume_checkpoint TEXT NOT NULL DEFAULT '',
    recovery_attempts INTEGER NOT NULL DEFAULT 0,
    last_failure_id TEXT NOT NULL DEFAULT '',
    cancellation_requested_at REAL NOT NULL DEFAULT 0
)
"""


@dataclass(frozen=True)
class TaskRecord:
    id: int
    task_key: str
    payload: dict[str, Any]
    status: str
    priority: int
    run_id: str
    worker_id: str
    lease_owner: str
    lease_expires_at: float
    heartbeat_at: float
    attempts: int
    max_retries: int
    available_at: float
    created_at: float
    updated_at: float
    last_error: str
    requires_human: bool
    exception_reason: str
    failure_kind: str
    recovery_action: str
    next_attempt_at: float
    resume_checkpoint: str
    recovery_attempts: int
    last_failure_id: str
    cancellation_requested_at: float


@dataclass(frozen=True)
class WorkerRecord:
    worker_id: str
    service_id: str
    pid: int
    status: str
    started_at: float
    heartbeat_at: float
    stopped_at: float
    current_task_id: int
    restart_count: int
    metadata: dict[str, Any]


class TaskQueue:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 1000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _begin_immediate(connection: sqlite3.Connection) -> None:
        for attempt, delay in enumerate((*SQLITE_BUSY_BACKOFF_SECONDS, 0.0), start=1):
            try:
                connection.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if not any(marker in message for marker in ("locked", "busy")) or attempt > len(
                    SQLITE_BUSY_BACKOFF_SECONDS
                ):
                    raise
                time.sleep(delay)

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                TASKS_TABLE_SQL.format(table="IF NOT EXISTS tasks")
                + """;
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(id),
                    event TEXT NOT NULL,
                    worker_id TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('starting','healthy','draining','stopped','stalled','failed')),
                    started_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    stopped_at REAL NOT NULL DEFAULT 0,
                    current_task_id INTEGER NOT NULL DEFAULT 0,
                    restart_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS workers_heartbeat_idx ON workers(status, heartbeat_at);
                """
            )
            self._migrate_tasks_table(connection)
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS tasks_claim_idx
                    ON tasks(status, available_at, priority DESC, id);
                CREATE INDEX IF NOT EXISTS tasks_lease_idx
                    ON tasks(status, lease_expires_at);
                CREATE INDEX IF NOT EXISTS tasks_recovery_idx
                    ON tasks(status, next_attempt_at, recovery_attempts);
                """
            )

    @staticmethod
    def _migrate_tasks_table(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()
        sql = str(row["sql"] if row is not None else "")
        columns = {str(item["name"]) for item in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        recovery_columns = {
            "failure_kind",
            "recovery_action",
            "next_attempt_at",
            "resume_checkpoint",
            "recovery_attempts",
            "last_failure_id",
            "cancellation_requested_at",
        }
        if "retry_wait" in sql and recovery_columns <= columns:
            if "lease_owner" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN lease_owner TEXT NOT NULL DEFAULT ''")
                connection.execute("UPDATE tasks SET lease_owner=worker_id WHERE worker_id!=''")
            return
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        TaskQueue._begin_immediate(connection)
        try:
            connection.execute("ALTER TABLE tasks RENAME TO tasks_legacy")
            connection.execute(TASKS_TABLE_SQL.format(table="tasks"))
            connection.execute(
                """
                INSERT INTO tasks(
                    id,task_key,payload_json,status,priority,run_id,worker_id,lease_owner,lease_expires_at,
                    heartbeat_at,attempts,max_retries,available_at,created_at,updated_at,last_error,
                    requires_human,exception_reason
                )
                SELECT id,task_key,payload_json,status,priority,run_id,worker_id,worker_id,lease_expires_at,
                    heartbeat_at,attempts,max_retries,available_at,created_at,updated_at,last_error,
                    requires_human,exception_reason
                FROM tasks_legacy
                """
            )
            connection.execute("DROP TABLE tasks_legacy")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA legacy_alter_table = OFF")
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def record(row: sqlite3.Row) -> TaskRecord:
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise ValueError("task payload must be a JSON object")
        return TaskRecord(
            id=int(row["id"]),
            task_key=str(row["task_key"]),
            payload=payload,
            status=str(row["status"]),
            priority=int(row["priority"]),
            run_id=str(row["run_id"]),
            worker_id=str(row["worker_id"]),
            lease_owner=str(row["lease_owner"]),
            lease_expires_at=float(row["lease_expires_at"]),
            heartbeat_at=float(row["heartbeat_at"]),
            attempts=int(row["attempts"]),
            max_retries=int(row["max_retries"]),
            available_at=float(row["available_at"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_error=str(row["last_error"]),
            requires_human=bool(row["requires_human"]),
            exception_reason=str(row["exception_reason"]),
            failure_kind=str(row["failure_kind"]),
            recovery_action=str(row["recovery_action"]),
            next_attempt_at=float(row["next_attempt_at"]),
            resume_checkpoint=str(row["resume_checkpoint"]),
            recovery_attempts=int(row["recovery_attempts"]),
            last_failure_id=str(row["last_failure_id"]),
            cancellation_requested_at=float(row["cancellation_requested_at"]),
        )

    @staticmethod
    def event(
        connection: sqlite3.Connection,
        task_id: int,
        event: str,
        worker_id: str = "",
        details: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO task_events(task_id,event,worker_id,details_json,created_at) VALUES(?,?,?,?,?)",
            (task_id, event, worker_id, json.dumps(details or {}, sort_keys=True), now or time.time()),
        )

    def enqueue(
        self,
        *,
        task_key: str,
        payload: dict[str, Any],
        priority: int = 0,
        max_retries: int = 2,
        run_id: str = "",
        supersede_awaiting_approval: bool = False,
    ) -> TaskRecord:
        if not task_key.strip():
            raise ValueError("task_key is required")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        now = time.time()
        with self.connect() as connection:
            self._begin_immediate(connection)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO tasks(
                    task_key,payload_json,status,priority,run_id,max_retries,available_at,created_at,updated_at
                ) VALUES(?,?,'queued',?,?,?,?,?,?)
                """,
                (task_key, payload_json, priority, run_id, max_retries, now, now, now),
            )
            row = connection.execute("SELECT * FROM tasks WHERE task_key=?", (task_key,)).fetchone()
            if row is None:
                connection.rollback()
                raise RuntimeError("failed to enqueue task")
            if cursor.rowcount == 1:
                self.event(connection, int(row["id"]), "enqueued", details={"priority": priority}, now=now)
            if supersede_awaiting_approval and run_id:
                superseded = connection.execute(
                    """
                    SELECT id FROM tasks
                    WHERE run_id=? AND status='awaiting_approval' AND id<>?
                    ORDER BY id
                    """,
                    (run_id, int(row["id"])),
                ).fetchall()
                connection.execute(
                    """
                    UPDATE tasks SET status='completed',updated_at=?,requires_human=0,
                        exception_reason='',recovery_action=''
                    WHERE run_id=? AND status='awaiting_approval' AND id<>?
                    """,
                    (now, run_id, int(row["id"])),
                )
                for previous in superseded:
                    self.event(
                        connection,
                        int(previous["id"]),
                        "superseded_by_resume",
                        details={"successor_task_id": int(row["id"]), "run_id": run_id},
                        now=now,
                    )
            connection.commit()
            return self.record(row)

    @staticmethod
    def _safe_run_dir(run_id: str) -> Path | None:
        if not run_id:
            return None
        candidate = (ROOT / ".agent-runs" / run_id).resolve()
        return (
            candidate
            if candidate.parent == (ROOT / ".agent-runs").resolve() and candidate.is_dir()
            else None
        )

    @staticmethod
    def _finalize_lease_recoveries(
        recoveries: list[tuple[Path | None, FailureRecord | None, str]],
    ) -> None:
        """Persist files and telemetry only after the SQLite transaction commits."""

        for run_dir, failure, status in recoveries:
            if run_dir is not None and failure is not None:
                try:
                    persist_failure(run_dir, failure)
                except OSError:
                    pass
            try:
                telemetry = safe_telemetry_runtime(
                    run_dir=run_dir,
                    service_name="ai-harness-queue",
                )
            except Exception:
                continue
            try:
                telemetry.queue_lease_expirations_total.add(1, {"outcome": status})
                if status == "dead_letter":
                    telemetry.dead_letters_total.add(1, {"failure.kind": "runtime_failure"})
            except Exception:
                pass
            finally:
                try:
                    telemetry.shutdown()
                except Exception:
                    pass

    def reclaim_expired(
        self,
        connection: sqlite3.Connection,
        now: float,
    ) -> list[tuple[Path | None, FailureRecord | None, str]]:
        rows = connection.execute(
            "SELECT * FROM tasks WHERE status IN ('claimed','leased','running') AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        recoveries: list[tuple[Path | None, FailureRecord | None, str]] = []
        for row in rows:
            run_id = str(row["run_id"])
            run_dir = self._safe_run_dir(run_id)
            if float(row["cancellation_requested_at"]) > 0:
                connection.execute(
                    """
                    UPDATE tasks SET status='cancelled',worker_id='',lease_owner='',lease_expires_at=0,heartbeat_at=0,
                        updated_at=?,requires_human=0,recovery_action='',exception_reason=''
                    WHERE id=?
                    """,
                    (now, int(row["id"])),
                )
                self.event(
                    connection,
                    int(row["id"]),
                    "cancelled_after_lease_expiry",
                    details={"run_id": str(row["run_id"])},
                    now=now,
                )
                recoveries.append((run_dir, None, "cancelled"))
                continue
            exhausted = int(row["recovery_attempts"]) >= int(row["max_retries"])
            status = "dead_letter" if exhausted else ("resuming" if str(row["run_id"]) else "retry_wait")
            reason = "worker lease expired after retry limit" if exhausted else "worker lease expired"
            failure: FailureRecord | None = None
            if run_dir is not None:
                try:
                    failure = FailureRecord.create(
                        run_id=str(row["run_id"]),
                        task_id=str(json.loads(str(row["payload_json"])).get("task_id", row["id"])),
                        role="worker",
                        stage="lease_recovery",
                        kind="runtime_failure",
                        error_type="LeaseExpired",
                        message=reason,
                        retryable=not exhausted,
                        repairable=False,
                        attempt=int(row["recovery_attempts"]) + 1,
                        max_attempts=max(1, int(row["max_retries"]) + 1),
                        checkpoint="last_safe_checkpoint",
                    )
                except (ValueError, json.JSONDecodeError):
                    failure = None
            failure_id = failure.failure_id if failure is not None else ""
            connection.execute(
                """
                UPDATE tasks SET status=?, worker_id='', lease_owner='', lease_expires_at=0, heartbeat_at=0,
                    available_at=?, next_attempt_at=?, updated_at=?, last_error=?, requires_human=?, exception_reason=?,
                    failure_kind='runtime_failure', recovery_action=?, recovery_attempts=recovery_attempts+1,
                    resume_checkpoint=CASE WHEN run_id!='' THEN 'last_safe_checkpoint' ELSE resume_checkpoint END,
                    last_failure_id=?
                WHERE id=?
                """,
                (
                    status,
                    now,
                    now,
                    now,
                    reason,
                    int(exhausted),
                    reason if exhausted else "",
                    "dead_letter" if exhausted else ("resume" if str(row["run_id"]) else "retry"),
                    failure_id,
                    int(row["id"]),
                ),
            )
            event = "lease_expired_resume" if status == "resuming" else status
            self.event(
                connection,
                int(row["id"]),
                event,
                details={"reason": reason, "run_id": str(row["run_id"])},
                now=now,
            )
            recoveries.append((run_dir, failure, status))
        return recoveries

    def claim(self, *, worker_id: str, lease_seconds: int = 120) -> TaskRecord | None:
        if not worker_id.strip() or lease_seconds <= 0:
            raise ValueError("worker_id and a positive lease_seconds are required")
        now = time.time()
        with self.connect() as connection:
            self._begin_immediate(connection)
            lease_recoveries = self.reclaim_expired(connection, now)
            row = connection.execute(
                """
                SELECT candidate.* FROM tasks AS candidate
                WHERE candidate.status IN ('queued','retry_wait','repairing','resuming')
                  AND candidate.available_at <= ?
                  AND (
                    COALESCE(json_extract(candidate.payload_json, '$.workspace_mode'), 'isolated') != 'current_branch'
                    OR NOT EXISTS (
                      SELECT 1 FROM tasks AS predecessor
                      WHERE predecessor.id < candidate.id
                        AND predecessor.status NOT IN ('completed','cancelled')
                        AND COALESCE(json_extract(predecessor.payload_json, '$.workspace_mode'), 'isolated') = 'current_branch'
                        AND json_extract(predecessor.payload_json, '$.repository') =
                            json_extract(candidate.payload_json, '$.repository')
                    )
                  )
                ORDER BY candidate.priority DESC, candidate.id ASC LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                connection.commit()
                self._finalize_lease_recoveries(lease_recoveries)
                return None
            task_id = int(row["id"])
            connection.execute(
                """
                UPDATE tasks SET status='claimed', worker_id=?, lease_owner=?, lease_expires_at=?, heartbeat_at=?,
                    attempts=attempts+1, updated_at=?, requires_human=0, exception_reason=''
                WHERE id=? AND status IN ('queued','retry_wait','repairing','resuming')
                """,
                (worker_id, worker_id, now + lease_seconds, now, now, task_id),
            )
            self.event(connection, task_id, "leased", worker_id, {"lease_seconds": lease_seconds}, now)
            claimed = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            connection.commit()
            self._finalize_lease_recoveries(lease_recoveries)
            return self.record(claimed)

    def mark_running(self, task_id: int, worker_id: str) -> bool:
        now = time.time()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='running', updated_at=? WHERE id=? AND worker_id=? AND status IN ('claimed','leased') AND lease_expires_at>=?",
                (now, task_id, worker_id, now),
            )
            if cursor.rowcount:
                self.event(connection, task_id, "running", worker_id, now=now)
            return cursor.rowcount == 1

    def heartbeat(self, task_id: int, worker_id: str, lease_seconds: int = 120) -> bool:
        now = time.time()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks SET heartbeat_at=?, lease_expires_at=?, updated_at=?
                WHERE id=? AND worker_id=? AND status IN ('claimed','leased','running') AND lease_expires_at>=?
                """,
                (now, now + lease_seconds, now, task_id, worker_id, now),
            )
            if cursor.rowcount:
                self.event(connection, task_id, "heartbeat", worker_id, now=now)
            return cursor.rowcount == 1

    def assign_run(self, task_id: int, worker_id: str, run_id: str) -> bool:
        if not run_id:
            raise ValueError("run_id is required")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks SET run_id=?,updated_at=?
                WHERE id=? AND worker_id=? AND status IN ('claimed','leased','running')
                """,
                (run_id, time.time(), task_id, worker_id),
            )
            return cursor.rowcount == 1

    def finish(
        self,
        *,
        task_id: int,
        worker_id: str,
        status: str,
        run_id: str = "",
        error: str = "",
        requires_human: bool = False,
        exception_reason: str = "",
        terminal_failure: bool = False,
    ) -> TaskRecord:
        if status not in {"completed", "blocked", "awaiting_approval", "dead_letter", "failed", "cancelled"}:
            raise ValueError(f"unsupported finish status: {status}")
        now = time.time()
        with self.connect() as connection:
            self._begin_immediate(connection)
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=? AND worker_id=? AND status IN ('claimed','leased','running')",
                (task_id, worker_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RuntimeError("worker does not own an active task lease")
            final_status = status
            human = requires_human or status in {"blocked", "awaiting_approval", "dead_letter", "failed"}
            reason = exception_reason
            available_at = now
            if status == "failed" and not terminal_failure:
                if int(row["recovery_attempts"]) < int(row["max_retries"]):
                    final_status = "retry_wait"
                    human = False
                    reason = ""
                    connection.execute(
                        "UPDATE tasks SET recovery_attempts=recovery_attempts+1,failure_kind='runtime_failure',recovery_action='retry',next_attempt_at=? WHERE id=?",
                        (now, task_id),
                    )
                else:
                    final_status = "dead_letter"
                    human = True
                    reason = exception_reason or "retry limit exceeded"
            connection.execute(
                """
                UPDATE tasks SET status=?, run_id=?, worker_id='', lease_owner='', lease_expires_at=0, heartbeat_at=0,
                    available_at=?, updated_at=?, last_error=?, requires_human=?, exception_reason=?
                WHERE id=?
                """,
                (final_status, run_id or str(row["run_id"]), available_at, now, error, int(human), reason, task_id),
            )
            self.event(connection, task_id, final_status, worker_id, {"error": error, "reason": reason}, now)
            finished = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            connection.commit()
            return self.record(finished)

    def requeue(
        self,
        *,
        task_id: int,
        worker_id: str,
        run_id: str,
        status: str,
        available_after: float,
        preserve_attempt_state: bool = True,
        failure_kind: str = "",
        recovery_action: str = "",
        resume_checkpoint: str = "",
        failure_id: str = "",
        error: str = "",
    ) -> TaskRecord:
        if status not in {"queued", "retry_wait", "repairing", "resuming"}:
            raise ValueError(f"unsupported recoverable status: {status}")
        now = time.time()
        with self.connect() as connection:
            self._begin_immediate(connection)
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=? AND worker_id=? AND status IN ('claimed','leased','running')",
                (task_id, worker_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RuntimeError("worker does not own an active task lease")
            attempts = int(row["attempts"]) if preserve_attempt_state else 0
            connection.execute(
                """
                UPDATE tasks SET status=?,run_id=?,worker_id='',lease_owner='',lease_expires_at=0,heartbeat_at=0,
                    attempts=?,available_at=?,next_attempt_at=?,updated_at=?,last_error=?,requires_human=0,
                    exception_reason='',failure_kind=?,recovery_action=?,resume_checkpoint=?,
                    recovery_attempts=recovery_attempts+1,last_failure_id=?
                WHERE id=?
                """,
                (
                    status,
                    run_id or str(row["run_id"]),
                    attempts,
                    max(now, available_after),
                    max(now, available_after),
                    now,
                    error,
                    failure_kind,
                    recovery_action,
                    resume_checkpoint,
                    failure_id,
                    task_id,
                ),
            )
            self.event(
                connection,
                task_id,
                status,
                worker_id,
                {"failure_kind": failure_kind, "recovery_action": recovery_action, "failure_id": failure_id},
                now,
            )
            updated = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            connection.commit()
            return self.record(updated)

    def schedule_retry(self, **kwargs: Any) -> TaskRecord:
        return self.requeue(status="retry_wait", **kwargs)

    def mark_repairing(self, **kwargs: Any) -> TaskRecord:
        return self.requeue(status="repairing", **kwargs)

    def mark_resuming(self, **kwargs: Any) -> TaskRecord:
        return self.requeue(status="resuming", **kwargs)

    def move_to_dead_letter(
        self,
        *,
        task_id: int,
        worker_id: str,
        run_id: str = "",
        error: str = "",
        failure_kind: str = "",
        failure_id: str = "",
    ) -> TaskRecord:
        now = time.time()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=? AND worker_id=? AND status IN ('claimed','leased','running')",
                (task_id, worker_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("worker does not own an active task lease")
            connection.execute(
                """
                UPDATE tasks SET status='dead_letter',run_id=?,worker_id='',lease_owner='',lease_expires_at=0,heartbeat_at=0,
                    updated_at=?,last_error=?,requires_human=1,exception_reason=?,failure_kind=?,
                    recovery_action='dead_letter',recovery_attempts=recovery_attempts+1,last_failure_id=?
                WHERE id=?
                """,
                (run_id or str(row["run_id"]), now, error, error or "recovery budget exhausted", failure_kind, failure_id, task_id),
            )
            self.event(connection, task_id, "dead_letter", worker_id, {"failure_id": failure_id}, now)
            updated = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self.record(updated)

    def list_recoverable(self) -> list[TaskRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE status IN ('queued','retry_wait','repairing','resuming') ORDER BY available_at,id"
            ).fetchall()
            return [self.record(row) for row in rows]

    def reclaim_expired_leases(self, *, now: float | None = None) -> list[TaskRecord]:
        """Recover expired active leases in one bounded SQLite transaction."""

        effective_now = time.time() if now is None else now
        with self.connect() as connection:
            self._begin_immediate(connection)
            rows = connection.execute(
                "SELECT id FROM tasks WHERE status IN ('claimed','leased','running') AND lease_expires_at < ?",
                (effective_now,),
            ).fetchall()
            task_ids = [int(row["id"]) for row in rows]
            lease_recoveries = self.reclaim_expired(connection, effective_now)
            if not task_ids:
                connection.commit()
                self._finalize_lease_recoveries(lease_recoveries)
                return []
            placeholders = ",".join("?" for _ in task_ids)
            recovered = connection.execute(
                f"SELECT * FROM tasks WHERE id IN ({placeholders}) ORDER BY id",
                task_ids,
            ).fetchall()
            connection.commit()
            self._finalize_lease_recoveries(lease_recoveries)
            return [self.record(row) for row in recovered]

    def recover_run(self, run_id: str, *, action: str) -> TaskRecord:
        status = {"retry": "retry_wait", "resume": "resuming"}.get(action)
        if status is None:
            raise ValueError("action must be retry or resume")
        now = time.time()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"queue task for run {run_id!r} was not found")
            current_status = str(row["status"])
            if current_status in ACTIVE_STATUSES:
                raise ValueError("cannot recover a task with an active lease")
            if current_status == "awaiting_approval":
                raise ValueError("approval-gated tasks must continue through the approval lifecycle")
            if current_status in {"completed", "cancelled"}:
                raise ValueError(f"cannot recover a terminal {current_status} task")
            connection.execute(
                """
                UPDATE tasks SET status=?,available_at=?,next_attempt_at=?,updated_at=?,requires_human=0,
                    exception_reason='',recovery_action=?,recovery_attempts=0 WHERE id=?
                """,
                (status, now, now, now, action, int(row["id"])),
            )
            self.event(connection, int(row["id"]), status, details={"manual": True, "action": action}, now=now)
            updated = connection.execute("SELECT * FROM tasks WHERE id=?", (int(row["id"]),)).fetchone()
            return self.record(updated)

    def mark_approval_expired(self, run_id: str) -> TaskRecord | None:
        """Mirror an expired approval into queue state so manual repair can proceed."""

        now = time.time()
        with self.connect() as connection:
            self._begin_immediate(connection)
            row = connection.execute(
                "SELECT * FROM tasks WHERE run_id=? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            if str(row["status"]) == "awaiting_approval":
                connection.execute(
                    """
                    UPDATE tasks SET status='blocked',updated_at=?,requires_human=1,
                        exception_reason='approval expired',recovery_action='retry'
                    WHERE id=?
                    """,
                    (now, int(row["id"])),
                )
                self.event(
                    connection,
                    int(row["id"]),
                    "approval_expired",
                    details={"run_id": run_id},
                    now=now,
                )
            updated = connection.execute("SELECT * FROM tasks WHERE id=?", (int(row["id"]),)).fetchone()
            connection.commit()
            return self.record(updated)

    def abort_run(self, run_id: str) -> TaskRecord:
        now = time.time()
        with self.connect() as connection:
            self._begin_immediate(connection)
            row = connection.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"queue task for run {run_id!r} was not found")
            current_status = str(row["status"])
            if current_status in ACTIVE_STATUSES:
                connection.execute(
                    "UPDATE tasks SET cancellation_requested_at=?,updated_at=? WHERE id=?",
                    (now, now, int(row["id"])),
                )
                self.event(
                    connection,
                    int(row["id"]),
                    "cancellation_requested",
                    str(row["worker_id"]),
                    {"manual": True},
                    now,
                )
                updated = connection.execute("SELECT * FROM tasks WHERE id=?", (int(row["id"]),)).fetchone()
                return self.record(updated)
            if current_status in {"completed", "cancelled"}:
                raise ValueError(f"cannot abort a terminal {current_status} task")
            connection.execute(
                """
                UPDATE tasks SET status='cancelled',cancellation_requested_at=?,updated_at=?,
                    requires_human=0,recovery_action='' WHERE id=?
                """,
                (now, now, int(row["id"])),
            )
            self.event(connection, int(row["id"]), "cancelled", details={"manual": True}, now=now)
            updated = connection.execute("SELECT * FROM tasks WHERE id=?", (int(row["id"]),)).fetchone()
            return self.record(updated)

    def cancellation_requested(self, task_id: int, worker_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT cancellation_requested_at FROM tasks
                WHERE id=? AND worker_id=? AND status IN ('claimed','leased','running')
                """,
                (task_id, worker_id),
            ).fetchone()
            return row is not None and float(row["cancellation_requested_at"]) > 0

    def get(self, task_id: int) -> TaskRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self.record(row) if row is not None else None

    def list(self, *, status: str = "", requires_human: bool = False) -> list[TaskRecord]:
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            if status not in ALL_STATUSES:
                raise ValueError(f"unknown task status: {status}")
            clauses.append("status=?")
            values.append(status)
        if requires_human:
            clauses.append("requires_human=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(f"SELECT * FROM tasks{where} ORDER BY priority DESC,id", values).fetchall()
            return [self.record(row) for row in rows]

    def stalled(self, *, stale_seconds: int = 180) -> list[TaskRecord]:
        now = time.time()
        threshold = now - max(1, stale_seconds)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tasks WHERE status IN ('claimed','leased','running')
                AND (lease_expires_at < ? OR heartbeat_at < ?)
                ORDER BY id
                """,
                (now, threshold),
            ).fetchall()
            return [self.record(row) for row in rows]

    def events(self, task_id: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if task_id is None:
                rows = connection.execute("SELECT * FROM task_events ORDER BY id").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM task_events WHERE task_id=? ORDER BY id",
                    (task_id,),
                ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "task_id": int(row["task_id"]),
                    "event": str(row["event"]),
                    "worker_id": str(row["worker_id"]),
                    "details": json.loads(str(row["details_json"])),
                    "created_at": float(row["created_at"]),
                }
                for row in rows
            ]

    @staticmethod
    def worker_record(row: sqlite3.Row) -> WorkerRecord:
        metadata = json.loads(str(row["metadata_json"]))
        return WorkerRecord(
            worker_id=str(row["worker_id"]),
            service_id=str(row["service_id"]),
            pid=int(row["pid"]),
            status=str(row["status"]),
            started_at=float(row["started_at"]),
            heartbeat_at=float(row["heartbeat_at"]),
            stopped_at=float(row["stopped_at"]),
            current_task_id=int(row["current_task_id"]),
            restart_count=int(row["restart_count"]),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def register_worker(
        self,
        *,
        worker_id: str,
        service_id: str,
        pid: int,
        restart_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> WorkerRecord:
        if not worker_id or not service_id or pid <= 0:
            raise ValueError("worker_id, service_id, and positive pid are required")
        now = time.time()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO workers(worker_id,service_id,pid,status,started_at,heartbeat_at,restart_count,metadata_json)
                VALUES(?,?,?,'starting',?,?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    service_id=excluded.service_id,pid=excluded.pid,status='starting',
                    started_at=excluded.started_at,heartbeat_at=excluded.heartbeat_at,stopped_at=0,
                    current_task_id=0,restart_count=excluded.restart_count,metadata_json=excluded.metadata_json
                """,
                (worker_id, service_id, pid, now, now, restart_count, json.dumps(metadata or {}, sort_keys=True)),
            )
            row = connection.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
            return self.worker_record(row)

    def worker_heartbeat(
        self,
        worker_id: str,
        *,
        status: str = "healthy",
        current_task_id: int = 0,
    ) -> bool:
        if status not in {"healthy", "draining", "failed"}:
            raise ValueError("invalid worker heartbeat status")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE workers SET status=?,heartbeat_at=?,current_task_id=? WHERE worker_id=?",
                (status, time.time(), current_task_id, worker_id),
            )
            return cursor.rowcount == 1

    def stop_worker(self, worker_id: str, *, status: str = "stopped") -> bool:
        if status not in {"stopped", "failed", "stalled"}:
            raise ValueError("invalid terminal worker status")
        now = time.time()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE workers SET status=?,heartbeat_at=?,stopped_at=?,current_task_id=0 WHERE worker_id=?",
                (status, now, now, worker_id),
            )
            return cursor.rowcount == 1

    def list_workers(self) -> list[WorkerRecord]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM workers ORDER BY service_id,worker_id").fetchall()
            return [self.worker_record(row) for row in rows]

    def stale_workers(self, *, stale_seconds: int = 60, mark: bool = False) -> list[WorkerRecord]:
        threshold = time.time() - max(1, stale_seconds)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workers WHERE status IN ('starting','healthy','draining') AND heartbeat_at < ?",
                (threshold,),
            ).fetchall()
            if mark and rows:
                ids = [str(row["worker_id"]) for row in rows]
                connection.executemany(
                    "UPDATE workers SET status='stalled' WHERE worker_id=?",
                    [(worker_id,) for worker_id in ids],
                )
                rows = connection.execute(
                    f"SELECT * FROM workers WHERE worker_id IN ({','.join('?' for _ in ids)}) ORDER BY worker_id",
                    ids,
                ).fetchall()
            return [self.worker_record(row) for row in rows]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)
    enqueue = subparsers.add_parser("enqueue")
    enqueue.add_argument("--task-key", required=True)
    enqueue.add_argument("--payload", required=True, help="JSON object")
    enqueue.add_argument("--priority", type=int, default=0)
    enqueue.add_argument("--max-retries", type=int, default=2)
    listing = subparsers.add_parser("list")
    listing.add_argument("--status", default="")
    listing.add_argument("--requires-human", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue = TaskQueue(args.db)
    if args.command == "enqueue":
        payload = json.loads(args.payload)
        if not isinstance(payload, dict):
            raise SystemExit("--payload must be a JSON object")
        record = queue.enqueue(
            task_key=args.task_key,
            payload=payload,
            priority=args.priority,
            max_retries=args.max_retries,
        )
        print(json.dumps(asdict(record), indent=2, ensure_ascii=False))
        return 0
    records = queue.list(status=args.status, requires_human=args.requires_human)
    print(json.dumps([asdict(record) for record in records], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
