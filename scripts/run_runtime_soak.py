#!/usr/bin/env python3
"""Enqueue and observe the real production-runtime soak manifest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.recovery.soak import validate_soak_manifest, validate_soak_report
from task_queue import ACTIVE_STATUSES, FINAL_STATUSES, TaskQueue
from worker_service import SERVICE_STATE, process_alive, read_state


RUNS_DIR = ROOT / ".agent-runs"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def publication_duplicates(run_dir: Path) -> tuple[int, int, bool, bool]:
    publication = read_json(run_dir / "artifacts" / "publication.json")
    workflow = read_json(run_dir / "workflow.json")
    if not publication or not publication.get("idempotency_key"):
        return 0, 0, False, False
    repository = Path(str(workflow.get("repository", "")))
    marker = str(publication["idempotency_key"])
    branch = str(publication.get("branch", workflow.get("branch", "")))
    try:
        commits = subprocess.run(
            ["git", "log", "--all", "--format=%B%x00", "--fixed-strings", "--grep", marker],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        commit_count = commits.stdout.count(marker) if commits.returncode == 0 else 0
        pull_requests = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--head", branch, "--json", "number"],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        values = json.loads(pull_requests.stdout) if pull_requests.returncode == 0 else None
        pr_count = len(values) if isinstance(values, list) else 0
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return 0, 0, False, True
    return max(0, commit_count - 1), max(0, pr_count - 1), True, True


def build_report(
    manifest: dict[str, Any],
    *,
    queue: TaskQueue,
    task_ids: list[int],
    runs_dir: Path,
    duration_seconds: int,
    service_survived: bool,
    timed_out: bool,
    minimum_duration_seconds: int = 7200,
) -> dict[str, Any]:
    tasks = [record for task_id in task_ids if (record := queue.get(task_id)) is not None]
    specs = {str(item["task_key"]): item for item in manifest["tasks"]}
    counts = Counter(str(item["category"]) for item in manifest["tasks"])
    recoverable_ok = True
    unrecoverable_ok = True
    lost_run_count = 0
    duplicate_commits = 0
    duplicate_prs = 0
    publication_runs = 0
    probes_complete = True
    for task in tasks:
        spec = specs[task.task_key]
        category = str(spec["category"])
        expected = "dead_letter" if category == "unrecoverable" else "completed"
        if category == "unrecoverable":
            unrecoverable_ok = unrecoverable_ok and task.status == expected
        else:
            recoverable_ok = recoverable_ok and task.status == expected
        run_dir = runs_dir / task.run_id
        workflow = read_json(run_dir / "workflow.json")
        if not task.run_id or workflow.get("run_id") != task.run_id or not workflow.get("worktree") or not workflow.get("branch"):
            lost_run_count += 1
        commit_duplicates, pr_duplicates, probed, publication_present = publication_duplicates(run_dir)
        if publication_present:
            publication_runs += 1
            probes_complete = probes_complete and probed
            duplicate_commits += commit_duplicates
            duplicate_prs += pr_duplicates
    hanging = sum(task.status in ACTIVE_STATUSES for task in queue.list())
    report = {
        "version": 1,
        "duration_seconds": duration_seconds,
        "task_count": len(tasks),
        "scenario_counts": dict(counts),
        "terminal_counts": dict(Counter(task.status for task in tasks)),
        "timed_out": timed_out,
        "invariants": {
            "worker_service_survived": service_survived,
            "recoverable_tasks_recovered": recoverable_ok,
            "unrecoverable_task_dead_lettered": unrecoverable_ok,
            "publication_probe_complete": probes_complete and publication_runs > 0,
            "run_identity_preserved": lost_run_count == 0,
            "no_hanging_leases": hanging == 0,
            "duplicate_commit_count": duplicate_commits,
            "duplicate_pr_count": duplicate_prs,
            "lost_run_count": lost_run_count,
            "hanging_lease_count": hanging,
            "publication_runs": publication_runs,
        },
        "tasks": [
            {"task_key": task.task_key, "run_id": task.run_id, "status": task.status, "attempts": task.attempts}
            for task in tasks
        ],
    }
    report["validation_errors"] = validate_soak_report(
        report, minimum_duration_seconds=minimum_duration_seconds
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path, default=ROOT / ".agent-queue" / "tasks.db")
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--minimum-duration-seconds", type=int, default=7200)
    parser.add_argument("--timeout-seconds", type=int, default=21600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = read_json(args.manifest)
    errors = validate_soak_manifest(manifest)
    if errors:
        print(json.dumps({"status": "failed", "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    queue = TaskQueue(args.db)
    records = [
        queue.enqueue(
            task_key=str(item["task_key"]),
            payload=dict(item["payload"]),
            max_retries=int(item.get("max_retries", 3)),
            run_id=str(item["payload"].get("run_id", "")),
        )
        for item in manifest["tasks"]
    ]
    started = time.monotonic()
    deadline = started + args.timeout_seconds
    service_survived = True
    timed_out = False
    while True:
        state = read_state(SERVICE_STATE)
        alive = process_alive(int(state.get("pid", 0) or 0))
        service_survived = service_survived and alive
        current = [queue.get(record.id) for record in records]
        terminal = all(item is not None and item.status in FINAL_STATUSES for item in current)
        elapsed = time.monotonic() - started
        if terminal and elapsed >= args.minimum_duration_seconds:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(args.poll_seconds)
    report = build_report(
        manifest,
        queue=queue,
        task_ids=[record.id for record in records],
        runs_dir=args.runs_dir,
        duration_seconds=int(time.monotonic() - started),
        service_survived=service_survived,
        timed_out=timed_out,
        minimum_duration_seconds=args.minimum_duration_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(args.output)
    print(json.dumps({"status": "passed" if not report["validation_errors"] else "failed", "report": str(args.output)}, indent=2))
    return 0 if not report["validation_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
