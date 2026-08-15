#!/usr/bin/env python3
"""Persistent registered worker service with health and graceful lifecycle management."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from approval_lifecycle import expire_approvals
from task_queue import DEFAULT_DB, TaskQueue
from worker_pool import WorkflowWorkerPool
from ai_harness.recovery.models import sanitized_message
from ai_harness.recovery.policy import load_recovery_policy
from ai_harness.build import harness_build_fingerprint


ROOT = Path(__file__).resolve().parents[1]
SERVICE_STATE = ROOT / ".agent-queue" / "worker-service.json"
SERVICE_LOG = ROOT / ".agent-queue" / "worker-service.log"


def write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_state(path: Path = SERVICE_STATE) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class WorkerService:
    def __init__(
        self,
        *,
        queue: TaskQueue,
        service_id: str,
        workers: int = 3,
        lease_seconds: int = 120,
        heartbeat_seconds: int = 10,
        poll_seconds: float = 2.0,
        state_path: Path = SERVICE_STATE,
        restart_count: int = 0,
        max_consecutive_failures: int | None = None,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.queue = queue
        self.service_id = service_id
        self.workers = workers
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.poll_seconds = poll_seconds
        self.state_path = state_path
        self.build_fingerprint = harness_build_fingerprint(ROOT)
        self.stop_event = threading.Event()
        self.total_restart_count = max(0, restart_count)
        self.consecutive_failure_count = 0
        configured_failures = (
            load_recovery_policy().max_consecutive_failures
            if max_consecutive_failures is None
            else max_consecutive_failures
        )
        self.max_consecutive_failures = max(1, configured_failures)
        self.max_restart_elapsed_seconds = load_recovery_policy().max_recovery_duration_seconds
        self.restart_recovery_started_at = 0.0
        self.worker_ids = [f"{service_id}-{index}" for index in range(1, workers + 1)]
        self.pool = WorkflowWorkerPool(
            queue=queue,
            workers=workers,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            worker_prefix=service_id,
            shutdown_requested=self.stop_event.is_set,
        )

    def register(self) -> None:
        for worker_id in self.worker_ids:
            self.queue.register_worker(
                worker_id=worker_id,
                service_id=self.service_id,
                pid=os.getpid(),
                restart_count=self.total_restart_count,
                metadata={"workers": self.workers},
            )
            self.queue.worker_heartbeat(worker_id)
        self.write_service_state("healthy")

    def write_service_state(self, status: str) -> None:
        write_state(
            self.state_path,
            {
                "service_id": self.service_id,
                "pid": os.getpid(),
                "status": status,
                "workers": self.worker_ids,
                "restart_count": self.total_restart_count,
                "total_restart_count": self.total_restart_count,
                "consecutive_failure_count": self.consecutive_failure_count,
                "restart_recovery_elapsed_seconds": (
                    max(0.0, time.monotonic() - self.restart_recovery_started_at)
                    if self.restart_recovery_started_at
                    else 0.0
                ),
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                "db": str(self.queue.path),
                "build_fingerprint": self.build_fingerprint,
            },
        )

    def heartbeat(self, status: str = "healthy") -> None:
        worker_status = "draining" if status == "draining" else "healthy"
        for worker_id in self.worker_ids:
            self.queue.worker_heartbeat(worker_id, status=worker_status)
        self.queue.stale_workers(stale_seconds=max(self.heartbeat_seconds * 3, 30), mark=True)
        self.write_service_state(status)

    def request_shutdown(self) -> None:
        self.stop_event.set()
        self.heartbeat("draining")

    def serve(self, *, once: bool = False) -> int:
        self.register()
        result_code = 0
        try:
            while not self.stop_event.is_set():
                try:
                    records = self.pool.run_wave()
                    degraded = any(
                        getattr(record, "status", "completed")
                        in {"retry_wait", "repairing", "resuming", "dead_letter", "failed"}
                        for record in records
                    )
                    self.heartbeat("degraded" if degraded else "healthy")
                    expire_approvals()
                    self.consecutive_failure_count = 0
                    self.restart_recovery_started_at = 0.0
                except Exception as exc:
                    if not self.restart_recovery_started_at:
                        self.restart_recovery_started_at = time.monotonic()
                    self.total_restart_count += 1
                    self.consecutive_failure_count += 1
                    self.write_service_error(exc)
                    status = (
                        "unhealthy"
                        if self.consecutive_failure_count >= self.max_consecutive_failures
                        else "degraded"
                    )
                    self.write_service_state(status)
                    result_code = 1
                    elapsed = time.monotonic() - self.restart_recovery_started_at
                    if (
                        once
                        or self.consecutive_failure_count >= self.max_consecutive_failures
                        or elapsed >= self.max_restart_elapsed_seconds
                    ):
                        break
                    delay = 30 if status == "unhealthy" else min(2 ** (self.consecutive_failure_count - 1), 10)
                    self.stop_event.wait(delay)
                    continue
                if once:
                    break
                if not records:
                    self.stop_event.wait(self.poll_seconds)
        finally:
            for worker_id in self.worker_ids:
                self.queue.stop_worker(worker_id)
            self.write_service_state("stopped")
        return result_code

    def write_service_error(self, exception: Exception) -> None:
        service_log = self.state_path.parent / "worker-service.log"
        try:
            service_log.parent.mkdir(parents=True, exist_ok=True)
            with service_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "service_id": self.service_id,
                            "error_type": type(exception).__name__,
                            "message": sanitized_message(exception),
                            "total_restart_count": self.total_restart_count,
                            "consecutive_failure_count": self.consecutive_failure_count,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            return

    def health(self) -> dict[str, Any]:
        workers = [record for record in self.queue.list_workers() if record.service_id == self.service_id]
        stale = {record.worker_id for record in self.queue.stale_workers(stale_seconds=max(self.heartbeat_seconds * 3, 30))}
        workers_alive = len(workers) == self.workers and not stale and all(
            record.status in {"starting", "healthy", "draining"} for record in workers
        )
        state_status = str(read_state(self.state_path).get("status", ""))
        status = "unhealthy" if not workers_alive else ("degraded" if state_status == "degraded" else "healthy")
        return {
            "service_id": self.service_id,
            "status": status,
            "pid": os.getpid(),
            "workers": [asdict(record) for record in workers],
            "stale_workers": sorted(stale),
            "restart_count": self.total_restart_count,
            "total_restart_count": self.total_restart_count,
            "consecutive_failure_count": self.consecutive_failure_count,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "start", "restart"):
        command = subparsers.add_parser(name)
        command.add_argument("--db", type=Path, default=DEFAULT_DB)
        command.add_argument("--service-id", default="")
        command.add_argument("--workers", type=int, default=3)
        command.add_argument("--lease-seconds", type=int, default=120)
        command.add_argument("--heartbeat-seconds", type=int, default=10)
        command.add_argument("--poll-seconds", type=float, default=2.0)
        command.add_argument("--restart-count", type=int, default=0)
        command.add_argument("--once", action="store_true")
    subparsers.add_parser("stop")
    subparsers.add_parser("status")
    subparsers.add_parser("health")
    return parser.parse_args()


def start_background(args: argparse.Namespace) -> int:
    state = read_state()
    if process_alive(int(state.get("pid", 0) or 0)):
        print(json.dumps({**state, "status": "already_running"}, indent=2))
        return 0
    service_id = args.service_id or f"worker-service-{uuid.uuid4().hex[:8]}"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "serve",
        "--db",
        str(args.db),
        "--service-id",
        service_id,
        "--workers",
        str(args.workers),
        "--lease-seconds",
        str(args.lease_seconds),
        "--heartbeat-seconds",
        str(args.heartbeat_seconds),
        "--poll-seconds",
        str(args.poll_seconds),
        "--restart-count",
        str(args.restart_count),
    ]
    SERVICE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SERVICE_LOG.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    write_state(
        SERVICE_STATE,
        {
            "service_id": service_id,
            "pid": process.pid,
            "status": "starting",
            "workers": [],
            "restart_count": args.restart_count,
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "db": str(args.db.resolve()),
        },
    )
    print(json.dumps(read_state(), indent=2))
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "restart":
        state = read_state()
        pid = int(state.get("pid", 0) or 0)
        if process_alive(pid):
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + load_recovery_policy().runtime_limits.shutdown_grace_seconds + 5
            while process_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if process_alive(pid):
                print(json.dumps({"status": "error", "error": "worker service did not stop"}))
                return 1
        args.service_id = args.service_id or str(state.get("service_id", ""))
        args.restart_count = int(state.get("restart_count", 0) or 0) + 1
        return start_background(args)
    if args.command == "start":
        return start_background(args)
    state = read_state()
    if args.command == "status":
        alive = process_alive(int(state.get("pid", 0) or 0))
        result = {**state, "alive": alive}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if alive else 1
    if args.command == "health":
        alive = process_alive(int(state.get("pid", 0) or 0))
        db_path = Path(str(state.get("db", DEFAULT_DB)))
        service_id = str(state.get("service_id", ""))
        queue = TaskQueue(db_path)
        workers = [record for record in queue.list_workers() if record.service_id == service_id]
        stale = queue.stale_workers(stale_seconds=30)
        stale_ids = sorted(record.worker_id for record in stale if record.service_id == service_id)
        workers_alive = alive and bool(service_id) and bool(workers) and not stale_ids and all(
            record.status in {"starting", "healthy", "draining"} for record in workers
        )
        status = "unhealthy" if not workers_alive else ("degraded" if state.get("status") == "degraded" else "healthy")
        result = {
            **state,
            "alive": alive,
            "status": status,
            "registered_workers": [asdict(record) for record in workers],
            "stale_workers": stale_ids,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if status in {"healthy", "degraded"} else 1
    if args.command == "stop":
        pid = int(state.get("pid", 0) or 0)
        if not process_alive(pid):
            print(json.dumps({**state, "status": "not_running"}, indent=2))
            return 0
        os.kill(pid, signal.SIGTERM)
        print(json.dumps({**state, "status": "stopping"}, indent=2))
        return 0
    service_id = args.service_id or f"worker-service-{uuid.uuid4().hex[:8]}"
    service = WorkerService(
        queue=TaskQueue(args.db),
        service_id=service_id,
        workers=args.workers,
        lease_seconds=args.lease_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
        poll_seconds=args.poll_seconds,
        restart_count=args.restart_count,
    )

    def handle_signal(_signum: int, _frame: Any) -> None:
        service.request_shutdown()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    return service.serve(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
