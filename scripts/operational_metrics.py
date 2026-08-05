#!/usr/bin/env python3
"""Expose bounded operational state without transcripts or source contents."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from list_runs import collect
from task_queue import DEFAULT_DB, TaskQueue
from worker_service import SERVICE_STATE, process_alive, read_state


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".agent-runs"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def jsonl_records(path: Path, *, max_bytes: int = 1_048_576) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            payload = handle.read(max_bytes)
    except OSError:
        return []
    if size > max_bytes:
        _, _, payload = payload.partition(b"\n")
    records: list[dict[str, Any]] = []
    for line in payload.decode("utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return round(ordered[index], 3)


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": round(max(values), 3) if values else None,
    }


def parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def run_cost(workflow: dict[str, Any], metrics: dict[str, Any]) -> float | None:
    for source in (metrics, workflow):
        value = source.get("cost_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return round(float(value), 6)
    roles = metrics.get("roles")
    if isinstance(roles, list):
        costs = [item.get("cost_usd") for item in roles if isinstance(item, dict)]
        numeric = [float(value) for value in costs if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if numeric and len(numeric) == len(costs):
            return round(sum(numeric), 6)
    return None


def run_summary(run_dir: Path) -> dict[str, Any] | None:
    workflow = read_json(run_dir / "workflow.json")
    if not workflow:
        return None
    metrics = read_json(run_dir / "metrics.json")
    budgets = workflow.get("budgets", {}) if isinstance(workflow.get("budgets"), dict) else {}
    approval = read_json(run_dir / "artifacts" / "approval.json")
    runner_events = jsonl_records(run_dir / "raw-events" / "workflow-runner.jsonl")
    errors = jsonl_records(run_dir / "errors.jsonl")
    spans = jsonl_records(run_dir / "raw-events" / "otel-spans.jsonl")
    loop_values = workflow.get("loops", {})
    repair_loop_iterations = 0
    if isinstance(loop_values, dict):
        for value in loop_values.values():
            if isinstance(value, dict):
                repair_loop_iterations += int(value.get("iterations", 0) or 0)
    iterations_seen = {
        int(event.get("iteration", 0) or 0)
        for event in runner_events
        if event.get("event") == "iteration_started"
    }
    workflow_iterations = len(iterations_seen)
    loop_iterations = repair_loop_iterations + workflow_iterations
    attempts: dict[tuple[int, str], int] = {}
    for event in runner_events:
        if "step" not in event:
            continue
        key = (int(event.get("iteration", 0) or 0), str(event.get("step", "")))
        attempts[key] = max(attempts.get(key, 0), int(event.get("attempt", 1) or 1))
    workflow_retries = sum(max(0, attempt - 1) for attempt in attempts.values())
    elapsed_seconds = float(workflow.get("elapsed_seconds", 0) or 0)
    if isinstance(metrics.get("duration_ms"), (int, float)):
        elapsed_seconds = float(metrics["duration_ms"]) / 1000
    publication = read_json(run_dir / "artifacts" / "publication.json") or read_json(run_dir / "publication.json")
    runtime_spans = [span for span in spans if span.get("name") == "ai_harness.runtime.execute"]
    runtime_timeouts = sum(span.get("name") == "ai_harness.runtime.timeout" for span in spans)
    runtime_failures = sum(span.get("status") == "error" for span in runtime_spans)
    reconciled_steps = publication.get("reconciled_steps", [])
    pr_time_seconds: float | None = None
    started_at = parse_timestamp(workflow.get("started_at"))
    publication_path = run_dir / "artifacts" / "publication.json"
    if not publication_path.exists():
        publication_path = run_dir / "publication.json"
    if publication.get("pr_created_or_updated") is True and started_at is not None and publication_path.exists():
        pr_time_seconds = round(max(0, publication_path.stat().st_mtime - started_at), 3)
    return {
        "run_id": run_dir.name,
        "task_id": str(workflow.get("task_id", "")),
        "project": str(workflow.get("project", "")),
        "status": str(workflow.get("execution_status", "unknown")),
        "risk_class": str(workflow.get("risk_class", "")),
        "role_count": int(workflow.get("role_count", 0) or 0),
        "tokens_used": int(workflow.get("tokens_used", metrics.get("tokens_used", 0)) or 0),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "cost_usd": run_cost(workflow, metrics),
        "loop_iterations": loop_iterations,
        "workflow_iterations": workflow_iterations,
        "repair_loop_iterations": repair_loop_iterations,
        "workflow_retries": workflow_retries,
        "recovery_attempts": int(
            workflow.get("recovery", {}).get("attempts", 0)
            if isinstance(workflow.get("recovery"), dict)
            else 0
        ),
        "resume_count": int(workflow.get("resume_count", 0) or 0),
        "failure_kind": str(workflow.get("failure_kind", "")),
        "failure_count": len(errors) or sum(event.get("event") == "workflow_failed" for event in runner_events),
        "runtime_executions": len(runtime_spans),
        "runtime_failures": runtime_failures,
        "runtime_timeouts": runtime_timeouts,
        "duplicate_side_effects_prevented": len(reconciled_steps) if isinstance(reconciled_steps, list) else 0,
        "pr_time_seconds": pr_time_seconds,
        "budgets": {
            "max_roles": int(budgets.get("max_roles", 0) or 0),
            "max_tokens": int(budgets.get("max_tokens", 0) or 0),
            "max_duration_seconds": int(budgets.get("max_duration_seconds", 0) or 0),
            "max_repair_iterations": int(budgets.get("max_repair_iterations", 0) or 0),
        },
        "approval_status": str(approval.get("status", "")),
        "updated_at": run_dir.joinpath("workflow.json").stat().st_mtime,
    }


def collect_metrics(
    *,
    runs_dir: Path = RUNS_DIR,
    db_path: Path = DEFAULT_DB,
    stale_seconds: int = 180,
) -> dict[str, Any]:
    runs = [
        summary
        for path in sorted(runs_dir.iterdir()) if runs_dir.exists() and path.is_dir()
        if (summary := run_summary(path))
    ] if runs_dir.exists() else []
    queue = TaskQueue(db_path)
    tasks = queue.list()
    events = queue.events()
    events_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_task[int(event["task_id"])].append(event)
    workers = queue.list_workers()
    stalled_worker_ids = {item.worker_id for item in queue.stale_workers(stale_seconds=stale_seconds)}
    now = time.time()
    exceptions = collect(
        runs_dir=runs_dir,
        db_path=db_path,
        requires_human=True,
        stale_seconds=stale_seconds,
    )
    service = read_state(SERVICE_STATE)
    service_pid = int(service.get("pid", 0) or 0)
    queue_waits: list[float] = []
    task_latencies: list[float] = []
    for task in tasks:
        task_events = events_by_task.get(task.id, [])
        leased = next((float(event["created_at"]) for event in task_events if event["event"] == "leased"), None)
        if leased is not None:
            queue_waits.append(max(0, leased - task.created_at))
        if task.status in {"completed", "blocked", "dead_letter"}:
            task_latencies.append(max(0, task.updated_at - task.created_at))
    run_latencies = [float(run["elapsed_seconds"]) for run in runs if float(run["elapsed_seconds"]) > 0]
    pr_latencies = [float(run["pr_time_seconds"]) for run in runs if run["pr_time_seconds"] is not None]
    known_costs = [float(run["cost_usd"]) for run in runs if run["cost_usd"] is not None]
    queue_retries = sum(max(0, task.attempts - 1) for task in tasks)
    workflow_retries = sum(int(run["workflow_retries"]) for run in runs)
    total_failures = sum(int(run["failure_count"]) for run in runs) + sum(task.status == "dead_letter" for task in tasks)
    active_workers = sum(item.status in {"starting", "healthy", "draining"} for item in workers)
    active_tasks = sum(item.status in {"claimed", "leased", "running"} for item in tasks)
    queue_depth = sum(item.status in {"queued", "retry_wait", "repairing", "resuming"} for item in tasks)
    completed_24h = sum(item.status == "completed" and item.updated_at >= now - 86_400 for item in tasks)
    from ai_harness.observability.store import trace_summary

    tracing = trace_summary(runs_dir)
    return {
        "schema_version": 1,
        "generated_at": now,
        "overview": {
            "queue_depth": queue_depth,
            "active_tasks": active_tasks,
            "active_workers": active_workers,
            "stalled_workers": len(stalled_worker_ids),
            "completed_last_24h": completed_24h,
            "human_interventions": len(exceptions),
        },
        "runs": {
            "counts": dict(Counter(str(run["status"]) for run in runs)),
            "items": runs,
        },
        "workers": {
            "counts": dict(Counter(item.status for item in workers)),
            "stalled": sorted(stalled_worker_ids),
            "items": [asdict(item) for item in workers],
        },
        "queue": {
            "counts": dict(Counter(item.status for item in tasks)),
            "items": [asdict(item) for item in tasks],
        },
        "leases": {
            "active": [
                {
                    "task_id": item.id,
                    "worker_id": item.worker_id,
                    "run_id": item.run_id,
                    "expires_at": item.lease_expires_at,
                    "expired": item.lease_expires_at < now,
                }
                for item in tasks
                if item.status in {"claimed", "leased", "running"}
            ]
        },
        "budgets": {
            "tokens_used": sum(int(run["tokens_used"]) for run in runs),
            "role_count": sum(int(run["role_count"]) for run in runs),
            "elapsed_seconds": round(sum(float(run["elapsed_seconds"]) for run in runs), 3),
        },
        "latency": {
            "queue_wait_seconds": distribution(queue_waits),
            "task_seconds": distribution(task_latencies),
            "run_seconds": distribution(run_latencies),
            "pr_time_seconds": distribution(pr_latencies),
        },
        "costs": {
            "known_usd": round(sum(known_costs), 6),
            "known_runs": len(known_costs),
            "unknown_runs": len(runs) - len(known_costs),
            "coverage": round(len(known_costs) / len(runs), 4) if runs else 0.0,
        },
        "retries": {
            "total": queue_retries + workflow_retries,
            "queue": queue_retries,
            "workflow": workflow_retries,
            "tasks_retried": sum(task.attempts > 1 for task in tasks),
        },
        "loops": {
            "total_iterations": sum(int(run["loop_iterations"]) for run in runs),
            "runs_with_loops": sum(int(run["loop_iterations"]) > 0 for run in runs),
        },
        "failures": {
            "total": total_failures,
            "run_failures": sum(int(run["failure_count"]) for run in runs),
            "dead_letters": sum(task.status == "dead_letter" for task in tasks),
            "by_run_status": dict(Counter(str(run["status"]) for run in runs if run["status"] in {"failed", "blocked"})),
        },
        "recovery": {
            "runtime_executions_total": sum(int(run["runtime_executions"]) for run in runs),
            "runtime_failures_total": sum(int(run["runtime_failures"]) for run in runs),
            "runtime_timeouts_total": sum(int(run["runtime_timeouts"]) for run in runs),
            "recovery_attempts_total": sum(item.recovery_attempts for item in tasks),
            "recovery_success_total": sum(run["status"] == "completed" and int(run["recovery_attempts"]) > 0 for run in runs),
            "recovery_exhausted_total": sum(item.status == "dead_letter" for item in tasks),
            "task_retries_total": sum(item.recovery_action == "retry" for item in tasks),
            "output_repairs_total": sum(run["failure_kind"] == "invalid_output" for run in runs),
            "resume_attempts_total": sum(int(run["resume_count"]) for run in runs),
            "resume_success_total": sum(run["status"] == "completed" and int(run["resume_count"]) > 0 for run in runs),
            "worker_crashes_total": sum(event["event"] == "lease_expired_resume" for event in events),
            "dead_letters_total": sum(item.status == "dead_letter" for item in tasks),
            "duplicate_side_effects_prevented_total": sum(
                int(run["duplicate_side_effects_prevented"]) for run in runs
            ),
            "queue_lease_expirations_total": sum(
                event["event"] in {"lease_expired_resume", "cancelled_after_lease_expiry"} for event in events
            ),
            "actions": dict(Counter(item.recovery_action for item in tasks if item.recovery_action)),
        },
        "tracing": tracing,
        "exceptions": [asdict(item) for item in exceptions],
        "service": {**service, "alive": process_alive(service_pid)},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--stale-seconds", type=int, default=180)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.dumps(
        collect_metrics(runs_dir=args.runs_dir, db_path=args.db, stale_seconds=args.stale_seconds),
        indent=2,
        ensure_ascii=False,
    ) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
