#!/usr/bin/env python3
"""Concurrent workflow workers backed by the SQLite task queue."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from opentelemetry.trace import Status, StatusCode

from task_queue import DEFAULT_DB, TaskQueue, TaskRecord


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.observability import NoOpTelemetryRuntime, TelemetryRuntime, safe_telemetry_runtime
from ai_harness.processes import run_managed_process
from ai_harness.recovery import RecoveryCoordinator, classify_failure, load_recovery_policy
from ai_harness.recovery.models import persist_failure, sanitized_message

RUNS_DIR = ROOT / ".agent-runs"


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str = ""
    error: str = ""
    failure_kind: str = ""
    recovery_action: str = ""
    retry_after_seconds: int = 0
    requires_human: bool = False
    exception_reason: str = ""
    failure_id: str = ""
    resume_checkpoint: str = ""


TaskHandler = Callable[[TaskRecord, str], WorkerOutcome]
ShutdownRequested = Callable[[], bool]


class WorkerLeaseLost(RuntimeError):
    """Raised after the workflow process group is stopped for a lost lease."""


def telemetry_run_dir(run_id: str) -> Path | None:
    candidate = (RUNS_DIR / run_id).resolve()
    return candidate if candidate.parent == RUNS_DIR.resolve() else None


def safe_telemetry_shutdown(telemetry: TelemetryRuntime | NoOpTelemetryRuntime | None) -> None:
    if telemetry is None:
        return
    try:
        telemetry.shutdown()
    except Exception:
        return


def safe_worker_heartbeat(queue: TaskQueue, worker_id: str, current_task_id: int = 0) -> None:
    try:
        queue.worker_heartbeat(worker_id, current_task_id=current_task_id)
    except Exception:
        return


def persist_workflow_cancellation(workflow_path: Path, *, run_id: str = "") -> None:
    """Atomically align authoritative workflow state with a user cancellation."""

    try:
        value = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow = value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        workflow = {}
    if run_id:
        workflow.setdefault("run_id", run_id)
    workflow["execution_status"] = "cancelled"
    workflow["cancellation_requested_at"] = datetime.now(timezone.utc).isoformat()
    workflow["cancellation_checkpoint"] = str(
        workflow.get("resume_from")
        or (f"before_{str(workflow.get('current_role', 'worker')).replace('-', '_')}")
    )
    temporary = workflow_path.with_suffix(".json.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(workflow_path)
    directory_fd = os.open(workflow_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def safe_payload(record: TaskRecord) -> dict[str, str]:
    allowed = {
        "task_id",
        "project",
        "repository",
        "branch",
        "base_branch",
        "run_id",
        "adapter_command",
        "runtime_provider",
        "runtime_command",
        "goal",
        "source",
        "event_id",
    }
    unknown = sorted(set(record.payload) - allowed)
    if unknown:
        raise ValueError("unsupported task payload fields: " + ", ".join(unknown))
    required = ("task_id", "repository")
    missing = [field for field in required if not isinstance(record.payload.get(field), str) or not record.payload[field]]
    if missing:
        raise ValueError("missing task payload fields: " + ", ".join(missing))
    if (
        record.payload.get("adapter_command") or record.payload.get("runtime_command")
    ) and os.environ.get("AGENT_HARNESS_TEST_MODE") != "1":
        raise ValueError("runtime command overrides are restricted to harness test mode")
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
        worker_prefix: str = "worker",
        shutdown_requested: ShutdownRequested | None = None,
    ) -> None:
        runtime_limits = load_recovery_policy().runtime_limits
        if workers < 1 or workers > 32:
            raise ValueError("workers must be between 1 and 32")
        if workers > runtime_limits.max_concurrent_subprocesses:
            raise ValueError("workers exceed the configured subprocess concurrency limit")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat_seconds must be positive and lower than lease_seconds")
        self.queue = queue
        self.workers = workers
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.handler = handler
        self.worker_prefix = worker_prefix
        self.shutdown_requested = shutdown_requested

    def run_workflow(self, record: TaskRecord, worker_id: str) -> WorkerOutcome:
        payload = safe_payload(record)
        run_id = payload.get("run_id") or record.run_id or f"queue-task-{record.id}"
        run_dir = telemetry_run_dir(run_id)
        if run_dir is None:
            return WorkerOutcome(
                status="failed",
                run_id=run_id,
                error="run id resolves outside the run store",
                failure_kind="policy_block",
                recovery_action="approval",
                requires_human=True,
                exception_reason="unsafe run id",
            )
        if not self.queue.assign_run(record.id, worker_id, run_id):
            return WorkerOutcome(
                status="failed",
                run_id=run_id,
                error="worker lost task lease before assigning run id",
                requires_human=True,
                exception_reason="worker lease lost",
            )
        command = [
            sys.executable,
            "scripts/run_workflow.py",
            "full_agent_workflow",
            "--run-id",
            run_id,
            "--task-id",
            payload["task_id"],
            "--goal",
            payload.get("goal", payload["task_id"]),
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
        if payload.get("runtime_provider"):
            command.extend(["--runtime-provider", payload["runtime_provider"]])
        if payload.get("runtime_command"):
            command.extend(["--runtime-command", payload["runtime_command"]])
        workflow_path = run_dir / "workflow.json"
        if workflow_path.exists():
            try:
                workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                workflow = {}
            status = workflow.get("execution_status") if isinstance(workflow, dict) else ""
            if status in {"retry_wait", "repairing", "resuming", "role_pending", "role_running", "role_output_received", "role_validating", "running"}:
                command.append("--resume")
            elif status == "awaiting_approval":
                return WorkerOutcome(
                    status="blocked",
                    run_id=run_id,
                    requires_human=True,
                    exception_reason="approval required",
                )
            elif status == "completed":
                return WorkerOutcome(status="completed", run_id=run_id)
        limits = load_recovery_policy().runtime_limits
        next_heartbeat_at = 0.0
        next_cancel_check_at = 0.0
        cancellation_seen = False

        def heartbeat() -> None:
            nonlocal next_heartbeat_at
            now = time.monotonic()
            if now < next_heartbeat_at:
                return
            self.queue.worker_heartbeat(worker_id, current_task_id=record.id)
            if not self.queue.heartbeat(record.id, worker_id, self.lease_seconds):
                raise WorkerLeaseLost("worker lease was lost")
            next_heartbeat_at = now + self.heartbeat_seconds

        def cancellation_requested() -> bool:
            nonlocal cancellation_seen, next_cancel_check_at
            if cancellation_seen:
                return True
            now = time.monotonic()
            if now >= next_cancel_check_at:
                cancellation_seen = self.queue.cancellation_requested(record.id, worker_id)
                next_cancel_check_at = now + min(1.0, float(self.heartbeat_seconds))
            return cancellation_seen

        try:
            process = run_managed_process(
                command,
                cwd=ROOT,
                stdout_path=run_dir / "raw-events" / "workflow.stdout.log",
                stderr_path=run_dir / "raw-events" / "workflow.stderr.log",
                env=TelemetryRuntime.inject_environment(dict(os.environ)),
                timeout_seconds=limits.workflow_timeout_seconds,
                idle_timeout_seconds=limits.idle_timeout_seconds,
                shutdown_grace_seconds=limits.shutdown_grace_seconds,
                max_output_bytes=limits.max_output_bytes,
                artifact_paths=(run_dir / "artifacts",),
                max_artifact_bytes=limits.max_artifact_bytes,
                max_open_files=limits.max_open_files,
                poll_seconds=min(0.25, float(self.heartbeat_seconds)),
                on_poll=heartbeat,
                cancel_requested=cancellation_requested,
                shutdown_requested=self.shutdown_requested,
                popen_factory=subprocess.Popen,
            )
        except WorkerLeaseLost as exc:
            return WorkerOutcome(
                status="failed",
                run_id=run_id,
                error=str(exc),
                requires_human=True,
                exception_reason="worker lease lost",
            )
        stdout, stderr = process.stdout, process.stderr
        if process.cancelled:
            persist_workflow_cancellation(workflow_path, run_id=run_id)
            return WorkerOutcome(status="cancelled", run_id=run_id, error="task cancelled by user")
        if process.shutdown_requested:
            return self._recovery_outcome(
                record,
                RuntimeError("workflow interrupted by graceful worker shutdown"),
                run_id=run_id,
                process_returncode=process.returncode,
            )
        if process.timed_out or process.idle_timed_out:
            return self._recovery_outcome(
                record,
                subprocess.TimeoutExpired(command, limits.workflow_timeout_seconds),
                run_id=run_id,
                process_returncode=124,
            )
        if process.output_limit_exceeded:
            return self._recovery_outcome(
                record,
                RuntimeError("workflow output exceeded the configured byte limit"),
                run_id=run_id,
                process_returncode=74,
            )
        if process.artifact_limit_exceeded:
            return self._recovery_outcome(
                record,
                RuntimeError("workflow artifacts exceeded the configured byte limit"),
                run_id=run_id,
                process_returncode=73,
            )
        if process.open_file_limit_exceeded:
            return self._recovery_outcome(
                record,
                RuntimeError("workflow open-file budget is exhausted"),
                run_id=run_id,
                process_returncode=72,
            )
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
        if execution_status in {"retry_wait", "repairing", "resuming"}:
            action = {
                "retry_wait": "retry",
                "repairing": "repair",
                "resuming": "resume",
            }[execution_status]
            return WorkerOutcome(
                status=execution_status,
                run_id=run_id,
                error=(stderr or stdout).strip(),
                failure_kind=str(workflow.get("failure_kind", "")),
                recovery_action=action,
                retry_after_seconds=int(workflow.get("retry_after_seconds", 0) or 0),
                failure_id=str(workflow.get("failure_id", "")),
                resume_checkpoint=str(workflow.get("resume_from", "")),
            )
        if execution_status in {"awaiting_approval", "blocked"}:
            blockers = workflow.get("blockers", [])
            reason = "; ".join(str(item) for item in blockers) if isinstance(blockers, list) else execution_status
            return WorkerOutcome(
                status="blocked",
                run_id=run_id,
                error=(stderr or stdout).strip(),
                requires_human=True,
                exception_reason=reason or execution_status,
                recovery_action="approval" if execution_status == "awaiting_approval" else "",
            )
        if execution_status == "dead_letter" or process.returncode == 30:
            return WorkerOutcome(
                status="dead_letter",
                run_id=run_id,
                error=(stderr or stdout).strip(),
                failure_kind=str(workflow.get("failure_kind", "")),
                recovery_action="dead_letter",
                requires_human=True,
                exception_reason=str(workflow.get("recovery_reason", "recovery budget exhausted")),
                failure_id=str(workflow.get("failure_id", "")),
            )
        if execution_status == "failed" or process.returncode in {40, 50}:
            return WorkerOutcome(
                status="failed",
                run_id=run_id,
                error=(stderr or stdout).strip(),
                failure_kind=str(workflow.get("failure_kind", "unrecoverable")),
                recovery_action="fail",
                requires_human=True,
                exception_reason=str(workflow.get("recovery_reason", "unrecoverable workflow failure")),
                failure_id=str(workflow.get("failure_id", "")),
            )
        return self._recovery_outcome(
            record,
            RuntimeError((stderr or stdout or f"workflow exit {process.returncode}").strip()),
            run_id=run_id,
            process_returncode=process.returncode,
        )

    def _recovery_outcome(
        self,
        record: TaskRecord,
        exception: Exception,
        *,
        run_id: str = "",
        process_returncode: int | None = None,
    ) -> WorkerOutcome:
        effective_run_id = run_id or str(record.payload.get("run_id") or record.run_id or f"queue-task-{record.id}")
        run_dir = telemetry_run_dir(effective_run_id)
        state: dict[str, object] = {"run_id": effective_run_id, "task_id": str(record.payload.get("task_id", record.id))}
        if run_dir is not None:
            workflow_path = run_dir / "workflow.json"
            try:
                loaded = json.loads(workflow_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        policy = load_recovery_policy()
        preliminary = classify_failure(
            exception,
            process_returncode,
            state,
            run_id=effective_run_id,
            task_id=str(record.payload.get("task_id", record.id)),
            role=str(state.get("current_role", "worker")),
            stage="worker_execute",
        )
        configured = policy.for_kind(preliminary.kind)
        attempt = record.recovery_attempts + 1
        failure = classify_failure(
            exception,
            process_returncode,
            state,
            run_id=effective_run_id,
            task_id=str(record.payload.get("task_id", record.id)),
            role=str(state.get("current_role", "worker")),
            stage="worker_execute",
            attempt=attempt,
            max_attempts=configured.max_attempts,
            checkpoint=str(state.get("resume_from", "before_worker_execute")),
        )
        decision = RecoveryCoordinator().decide(failure, state, policy)
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            persist_failure(run_dir, failure)
        return WorkerOutcome(
            status=decision.next_status,
            run_id=effective_run_id,
            error=failure.message,
            failure_kind=failure.kind,
            recovery_action=decision.action,
            retry_after_seconds=decision.delay_seconds,
            requires_human=decision.requires_human,
            exception_reason=decision.reason,
            failure_id=failure.failure_id,
            resume_checkpoint=failure.checkpoint,
        )

    def _invoke_handler(self, record: TaskRecord, worker_id: str) -> WorkerOutcome:
        try:
            outcome = self.handler(record, worker_id) if self.handler is not None else self.run_workflow(record, worker_id)
        except Exception as exc:
            return self._recovery_outcome(record, exc)
        if outcome.status == "failed" and not outcome.recovery_action:
            return self._recovery_outcome(record, RuntimeError(outcome.error or outcome.exception_reason or "worker handler failed"), run_id=outcome.run_id)
        return outcome

    def _finish_outcome(self, record: TaskRecord, worker_id: str, outcome: WorkerOutcome) -> TaskRecord:
        available_after = time.time() + max(0, outcome.retry_after_seconds)
        common = {
            "task_id": record.id,
            "worker_id": worker_id,
            "run_id": outcome.run_id,
            "available_after": available_after,
            "preserve_attempt_state": True,
            "failure_kind": outcome.failure_kind,
            "recovery_action": outcome.recovery_action,
            "resume_checkpoint": outcome.resume_checkpoint,
            "failure_id": outcome.failure_id,
            "error": outcome.error,
        }
        if outcome.recovery_action == "retry":
            return self.queue.schedule_retry(**common)
        if outcome.recovery_action == "repair":
            return self.queue.mark_repairing(**common)
        if outcome.recovery_action == "resume":
            return self.queue.mark_resuming(**common)
        if outcome.recovery_action == "dead_letter" or outcome.status == "dead_letter":
            return self.queue.move_to_dead_letter(
                task_id=record.id,
                worker_id=worker_id,
                run_id=outcome.run_id,
                error=outcome.error or outcome.exception_reason,
                failure_kind=outcome.failure_kind,
                failure_id=outcome.failure_id,
            )
        status = "awaiting_approval" if outcome.recovery_action == "approval" else outcome.status
        return self.queue.finish(
            task_id=record.id,
            worker_id=worker_id,
            status=status,
            run_id=outcome.run_id,
            error=outcome.error,
            requires_human=outcome.requires_human,
            exception_reason=outcome.exception_reason,
            terminal_failure=outcome.recovery_action == "fail",
        )

    def process_one(self, worker_number: int) -> TaskRecord | None:
        worker_id = f"{self.worker_prefix}-{worker_number}"
        record: TaskRecord | None = None
        telemetry: TelemetryRuntime | NoOpTelemetryRuntime | None = None
        started = time.monotonic()
        try:
            record = self.queue.claim(worker_id=worker_id, lease_seconds=self.lease_seconds)
            if record is None:
                return None
            if not self.queue.mark_running(record.id, worker_id):
                return self.queue.get(record.id)
            safe_worker_heartbeat(self.queue, worker_id, record.id)
            predicted_run_id = str(record.payload.get("run_id") or record.run_id or f"queue-task-{record.id}")
            try:
                telemetry = safe_telemetry_runtime(
                    run_dir=telemetry_run_dir(predicted_run_id),
                    service_name="ai-harness-worker",
                    service_instance_id=worker_id,
                )
            except Exception:
                telemetry = None
            outcome: WorkerOutcome | None = None
            if telemetry is not None:
                try:
                    with telemetry.span(
                        "ai_harness.worker.task",
                        {
                            "task.id": record.id,
                            "task.attempt": record.attempts,
                            "worker.id": worker_id,
                            "queue.wait_seconds": max(0, time.time() - record.created_at),
                        },
                    ) as span:
                        outcome = self._invoke_handler(record, worker_id)
                        span.set_attribute("task.outcome", outcome.status)
                        span.set_attribute("task.requires_human", outcome.requires_human)
                        if outcome.status not in {"completed", "blocked", "awaiting_approval"}:
                            span.set_status(Status(StatusCode.ERROR))
                except Exception:
                    pass
            if outcome is None:
                outcome = self._invoke_handler(record, worker_id)
            if telemetry is not None:
                try:
                    if outcome.status not in {"completed", "blocked", "awaiting_approval"}:
                        telemetry.failure_counter.add(1, {"operation": "worker.task", "outcome": outcome.status})
                    telemetry.task_counter.add(1, {"outcome": outcome.status})
                    telemetry.duration_histogram.record(max(0, time.monotonic() - started), {"operation": "worker.task"})
                    if outcome.recovery_action == "resume":
                        telemetry.resume_attempts_total.add(1, {"failure.kind": outcome.failure_kind})
                    if outcome.recovery_action == "dead_letter" or outcome.status == "dead_letter":
                        telemetry.dead_letters_total.add(1, {"failure.kind": outcome.failure_kind})
                except Exception:
                    pass
            return self._finish_outcome(record, worker_id, outcome)
        except Exception as exc:
            if record is None:
                self.record_pool_failure(worker_number, exc)
                return None
            try:
                outcome = self._recovery_outcome(record, exc)
                return self._finish_outcome(record, worker_id, outcome)
            except Exception as nested:
                self.record_pool_failure(worker_number, nested, run_id=record.run_id)
                return self.queue.get(record.id)
        finally:
            safe_telemetry_shutdown(telemetry)
            safe_worker_heartbeat(self.queue, worker_id, 0)

    def record_pool_failure(self, worker_number: int, exception: Exception, *, run_id: str = "") -> None:
        path = self.queue.path.parent / "worker-pool-errors.jsonl"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "worker_number": worker_number,
                            "error_type": type(exception).__name__,
                            "message": sanitized_message(exception),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass
        try:
            telemetry = safe_telemetry_runtime(
                run_dir=telemetry_run_dir(run_id) if run_id else None,
                service_name="ai-harness-worker",
                service_instance_id=f"{self.worker_prefix}-{worker_number}",
            )
        except Exception:
            return
        try:
            with telemetry.span(
                "ai_harness.worker.crash",
                {"worker.number": worker_number, "error.type": type(exception).__name__},
            ) as span:
                span.set_status(Status(StatusCode.ERROR))
            telemetry.worker_crashes_total.add(1, {"error.type": type(exception).__name__})
        except Exception:
            pass
        finally:
            safe_telemetry_shutdown(telemetry)

    def run_wave(self) -> list[TaskRecord]:
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="agent-worker") as executor:
            futures: dict[Future[TaskRecord | None], int] = {
                executor.submit(self.process_one, number): number for number in range(1, self.workers + 1)
            }
            records: list[TaskRecord] = []
            for future in as_completed(futures):
                worker_number = futures[future]
                try:
                    record = future.result()
                except Exception as exc:
                    self.record_pool_failure(worker_number, exc)
                    continue
                if record is not None:
                    records.append(record)
        return records

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
