#!/usr/bin/env python3
"""Expose compact operational state without transcripts or source contents."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict
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


def run_summary(run_dir: Path) -> dict[str, Any] | None:
    workflow = read_json(run_dir / "workflow.json")
    if not workflow:
        return None
    budgets = workflow.get("budgets", {}) if isinstance(workflow.get("budgets"), dict) else {}
    approval = read_json(run_dir / "artifacts" / "approval.json")
    return {
        "run_id": run_dir.name,
        "task_id": str(workflow.get("task_id", "")),
        "project": str(workflow.get("project", "")),
        "status": str(workflow.get("execution_status", "unknown")),
        "risk_class": str(workflow.get("risk_class", "")),
        "role_count": int(workflow.get("role_count", 0) or 0),
        "tokens_used": int(workflow.get("tokens_used", 0) or 0),
        "elapsed_seconds": int(workflow.get("elapsed_seconds", 0) or 0),
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
    return {
        "generated_at": now,
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
                if item.status in {"leased", "running"}
            ]
        },
        "budgets": {
            "tokens_used": sum(int(run["tokens_used"]) for run in runs),
            "role_count": sum(int(run["role_count"]) for run in runs),
            "elapsed_seconds": sum(int(run["elapsed_seconds"]) for run in runs),
        },
        "exceptions": [asdict(item) for item in exceptions],
        "service": {**service, "alive": process_alive(service_pid)},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--stale-seconds", type=int, default=180)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(
        json.dumps(
            collect_metrics(runs_dir=args.runs_dir, db_path=args.db, stale_seconds=args.stale_seconds),
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
