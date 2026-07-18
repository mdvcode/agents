#!/usr/bin/env python3
"""SQLite-backed task queue with leases, heartbeats, retries, and dead letters."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / ".agent-queue" / "tasks.db"
ACTIVE_STATUSES = {"leased", "running"}
FINAL_STATUSES = {"completed", "blocked", "dead_letter"}
ALL_STATUSES = {"queued", *ACTIVE_STATUSES, *FINAL_STATUSES}


@dataclass(frozen=True)
class TaskRecord:
    id: int
    task_key: str
    payload: dict[str, Any]
    status: str
    priority: int
    run_id: str
    worker_id: str
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


class TaskQueue:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued','leased','running','completed','blocked','dead_letter')),
                    priority INTEGER NOT NULL DEFAULT 0,
                    run_id TEXT NOT NULL DEFAULT '',
                    worker_id TEXT NOT NULL DEFAULT '',
                    lease_expires_at REAL NOT NULL DEFAULT 0,
                    heartbeat_at REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 2,
                    available_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    requires_human INTEGER NOT NULL DEFAULT 0,
                    exception_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS tasks_claim_idx
                    ON tasks(status, available_at, priority DESC, id);
                CREATE INDEX IF NOT EXISTS tasks_lease_idx
                    ON tasks(status, lease_expires_at);
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(id),
                    event TEXT NOT NULL,
                    worker_id TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                """
            )

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
    ) -> TaskRecord:
        if not task_key.strip():
            raise ValueError("task_key is required")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        now = time.time()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
            connection.commit()
            return self.record(row)

    def reclaim_expired(self, connection: sqlite3.Connection, now: float) -> None:
        rows = connection.execute(
            "SELECT * FROM tasks WHERE status IN ('leased','running') AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        for row in rows:
            exhausted = int(row["attempts"]) >= int(row["max_retries"]) + 1
            status = "dead_letter" if exhausted else "queued"
            reason = "worker lease expired after retry limit" if exhausted else "worker lease expired"
            connection.execute(
                """
                UPDATE tasks SET status=?, worker_id='', lease_expires_at=0, heartbeat_at=0,
                    available_at=?, updated_at=?, last_error=?, requires_human=?, exception_reason=?
                WHERE id=?
                """,
                (status, now, now, reason, int(exhausted), reason if exhausted else "", int(row["id"])),
            )
            self.event(connection, int(row["id"]), status, details={"reason": reason}, now=now)

    def claim(self, *, worker_id: str, lease_seconds: int = 120) -> TaskRecord | None:
        if not worker_id.strip() or lease_seconds <= 0:
            raise ValueError("worker_id and a positive lease_seconds are required")
        now = time.time()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.reclaim_expired(connection, now)
            row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE status='queued' AND available_at <= ?
                ORDER BY priority DESC, id ASC LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            task_id = int(row["id"])
            connection.execute(
                """
                UPDATE tasks SET status='leased', worker_id=?, lease_expires_at=?, heartbeat_at=?,
                    attempts=attempts+1, updated_at=?, requires_human=0, exception_reason=''
                WHERE id=? AND status='queued'
                """,
                (worker_id, now + lease_seconds, now, now, task_id),
            )
            self.event(connection, task_id, "leased", worker_id, {"lease_seconds": lease_seconds}, now)
            claimed = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            connection.commit()
            return self.record(claimed)

    def mark_running(self, task_id: int, worker_id: str) -> bool:
        now = time.time()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='running', updated_at=? WHERE id=? AND worker_id=? AND status='leased' AND lease_expires_at>=?",
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
                WHERE id=? AND worker_id=? AND status IN ('leased','running') AND lease_expires_at>=?
                """,
                (now, now + lease_seconds, now, task_id, worker_id, now),
            )
            if cursor.rowcount:
                self.event(connection, task_id, "heartbeat", worker_id, now=now)
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
        retry_delay_seconds: int = 0,
    ) -> TaskRecord:
        if status not in {"completed", "blocked", "failed"}:
            raise ValueError(f"unsupported finish status: {status}")
        now = time.time()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=? AND worker_id=? AND status IN ('leased','running')",
                (task_id, worker_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise RuntimeError("worker does not own an active task lease")
            final_status = status
            human = requires_human or status == "blocked"
            reason = exception_reason
            available_at = now
            if status == "failed":
                if int(row["attempts"]) <= int(row["max_retries"]):
                    final_status = "queued"
                    available_at = now + max(0, retry_delay_seconds)
                    human = False
                    reason = ""
                else:
                    final_status = "dead_letter"
                    human = True
                    reason = exception_reason or "retry limit exceeded"
            connection.execute(
                """
                UPDATE tasks SET status=?, run_id=?, worker_id='', lease_expires_at=0, heartbeat_at=0,
                    available_at=?, updated_at=?, last_error=?, requires_human=?, exception_reason=?
                WHERE id=?
                """,
                (final_status, run_id or str(row["run_id"]), available_at, now, error, int(human), reason, task_id),
            )
            self.event(connection, task_id, final_status, worker_id, {"error": error, "reason": reason}, now)
            finished = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            connection.commit()
            return self.record(finished)

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
                SELECT * FROM tasks WHERE status IN ('leased','running')
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
