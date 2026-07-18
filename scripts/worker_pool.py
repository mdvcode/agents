#!/usr/bin/env python3
"""Concurrent workflow workers backed by the SQLite task queue."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from task_queue import DEFAULT_DB, TaskQueue, TaskRecord


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".agent-runs"


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str = ""
    error: str = ""
    requires_human: bool = False
    exception_reason: str = ""


TaskHandler = Callable[[TaskRecord, str], WorkerOutcome]


def safe_payload(record: TaskRecord) -> dict[str, str]:
    allowed = {
        "task_id",
        "project",
        "repository",
        "branch",
        "base_branch",
        "run_id",
        "adapter_command",
    }
    unknown = sorted(set(record.payload) - allowed)
    if unknown:
        raise ValueError("unsupported task payload fields: " + ", ".join(unknown))
    required = ("task_id", "repository")
    missing = [field for field in required if not isinstance(record.payload.get(field), str) or not record.payload[field]]
    if missing:
        raise ValueError("missing task payload fields: " + ", ".join(missing))
    if record.payload.get("adapter_command") and os.environ.get("AGENT_HARNESS_TEST_MODE") != "1":
        raise ValueError("adapter_command overrides are restricted to harness test mode")
    return {key: str(value) for key, value in record.payload.items()}


class WorkflowWorkerPool:
    def __init__(
        self,
        *,
        queue: TaskQueue,
        workers: int = 3,
        lease_seconds: int = 120,
        heartbeat_seconds: int = 10,
        handler: TaskHandler | None = None,
    ) -> None:
        if workers < 1 or workers > 32:
            raise ValueError("workers must be between 1 and 32")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat_seconds must be positive and lower than lease_seconds")
        self.queue = queue
        self.workers = workers
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.handler = handler

    def run_workflow(self, record: TaskRecord, worker_id: str) -> WorkerOutcome:
        payload = safe_payload(record)
        run_id = payload.get("run_id") or datetime.now(timezone.utc).strftime(
            f"%Y%m%dT%H%M%S.%fZ-queue-{record.id}"
        )
        command = [
            sys.executable,
            "scripts/run_workflow.py",
            "full_agent_workflow",
            "--run-id",
            run_id,
            "--task-id",
            payload["task_id"],
            "--project",
            payload.get("project", "agent_workspace"),
            "--repo",
            payload["repository"],
            "--branch",
            payload.get("branch", f"issue/{payload['task_id']}"),
            "--base-branch",
            payload.get("base_branch", "main"),
        ]
        if payload.get("adapter_command"):
            command.extend(["--adapter-command", payload["adapter_command"]])
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        while process.poll() is None:
            time.sleep(self.heartbeat_seconds)
            if not self.queue.heartbeat(record.id, worker_id, self.lease_seconds):
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                return WorkerOutcome(
                    status="failed",
                    run_id=run_id,
                    error=(stderr or stdout or "worker lease was lost").strip(),
                    requires_human=True,
                    exception_reason="worker lease lost",
                )
        stdout, stderr = process.communicate()
        workflow_path = RUNS_DIR / run_id / "workflow.json"
        workflow: dict[str, object] = {}
        if workflow_path.exists():
            try:
                loaded = json.loads(workflow_path.read_text(encoding="utf-8"))
                workflow = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                workflow = {}
        execution_status = str(workflow.get("execution_status", ""))
        if process.returncode == 0 and execution_status == "completed":
            return WorkerOutcome(status="completed", run_id=run_id)
        if execution_status in {"awaiting_approval", "blocked"}:
            blockers = workflow.get("blockers", [])
            reason = "; ".join(str(item) for item in blockers) if isinstance(blockers, list) else execution_status
            return WorkerOutcome(
                status="blocked",
                run_id=run_id,
                error=(stderr or stdout).strip(),
                requires_human=True,
                exception_reason=reason or execution_status,
            )
        return WorkerOutcome(
            status="failed",
            run_id=run_id,
            error=(stderr or stdout or f"workflow exit {process.returncode}").strip(),
            exception_reason="workflow execution failed",
        )

    def process_one(self, worker_number: int) -> TaskRecord | None:
        worker_id = f"worker-{worker_number}"
        record = self.queue.claim(worker_id=worker_id, lease_seconds=self.lease_seconds)
        if record is None:
            return None
        if not self.queue.mark_running(record.id, worker_id):
            return self.queue.get(record.id)
        try:
            outcome = (
                self.handler(record, worker_id)
                if self.handler is not None
                else self.run_workflow(record, worker_id)
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            outcome = WorkerOutcome(
                status="failed",
                error=str(exc),
                exception_reason="worker handler failed",
            )
        return self.queue.finish(
            task_id=record.id,
            worker_id=worker_id,
            status=outcome.status,
            run_id=outcome.run_id,
            error=outcome.error,
            requires_human=outcome.requires_human,
            exception_reason=outcome.exception_reason,
        )

    def run_wave(self) -> list[TaskRecord]:
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="agent-worker") as executor:
            results = list(executor.map(self.process_one, range(1, self.workers + 1)))
        return [record for record in results if record is not None]

    def drain(self) -> list[TaskRecord]:
        processed: list[TaskRecord] = []
        while True:
            wave = self.run_wave()
            if not wave:
                return processed
            processed.extend(wave)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--heartbeat-seconds", type=int, default=10)
    parser.add_argument("--once", action="store_true", help="Process one task per worker and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pool = WorkflowWorkerPool(
        queue=TaskQueue(args.db),
        workers=args.workers,
        lease_seconds=args.lease_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    records = pool.run_wave() if args.once else pool.drain()
    print(json.dumps([record.__dict__ for record in records], indent=2, ensure_ascii=False))
    return 0 if all(record.status in {"completed", "blocked"} for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
