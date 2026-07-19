#!/usr/bin/env python3
"""Verify concurrent real workflows, isolation, gates, and exception handling for Step 2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from task_queue import DEFAULT_DB, TaskQueue
from workflow_router import required_gate_roles


VERIFICATION_PLANE = {
    "security-agent",
    "reviewer",
    "architecture-consistency-agent",
    "semantic-conflict-agent",
    "frontend-qa-agent",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def completed_roles(workflow: dict[str, Any]) -> set[str]:
    return {
        str(entry.get("role", ""))
        for entry in workflow.get("roles", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("result"), dict)
        and entry["result"].get("status") == "completed"
    }


def tool_audit_is_allowed(path: Path) -> bool:
    if not path.exists():
        return False
    found = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        found = True
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(entry, dict) or entry.get("allowed") is not True:
            return False
    return found


def maximum_concurrency(events: list[dict[str, Any]]) -> int:
    points: list[tuple[float, int]] = []
    starts: dict[int, float] = {}
    for event in events:
        task_id = int(event["task_id"])
        if event["event"] == "running":
            starts[task_id] = float(event["created_at"])
        elif event["event"] in {"queued", "completed", "blocked", "dead_letter"} and task_id in starts:
            points.append((starts.pop(task_id), 1))
            points.append((float(event["created_at"]), -1))
    active = 0
    maximum = 0
    for _timestamp, delta in sorted(points, key=lambda item: (item[0], -item[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def verify_run(runs_dir: Path, run_id: str, queue_status: str) -> dict[str, Any]:
    run_dir = runs_dir / run_id
    workflow = read_json(run_dir / "workflow.json")
    metrics = read_json(run_dir / "metrics.json")
    publication = read_json(run_dir / "artifacts" / "publication.json")
    issue = read_json(run_dir / "artifacts" / "issue.json")
    blockers: list[str] = []
    executor = workflow.get("runtime", workflow.get("executor", {}))
    provider = executor.get("provider") if isinstance(executor, dict) else ""
    real_executor = (
        isinstance(executor, dict)
        and (provider == "codex-cli" or executor.get("kind") == "codex_cli")
        and executor.get("production") is True
        and (run_dir / "raw-events" / "planner.jsonl").exists()
        and int(metrics.get("tokens_used", 0) or 0) > 0
    )
    if not real_executor:
        blockers.append("real Codex executor evidence is missing")
    loops = workflow.get("loops", {})
    for loop in ("quality_repair", "review_repair", "ci_repair", "frontend_verification_repair"):
        if not isinstance(loops, dict) or loop not in loops:
            blockers.append(f"workflow loop state missing: {loop}")
    roles = completed_roles(workflow)
    required_verifiers = set(required_gate_roles(workflow, run_dir / "artifacts")) & VERIFICATION_PLANE
    missing_verifiers = sorted(required_verifiers - roles) if queue_status == "completed" else []
    if missing_verifiers:
        blockers.append("independent verifiers missing: " + ", ".join(missing_verifiers))
    worktree = str(workflow.get("worktree", ""))
    if not worktree or issue.get("worktree") != worktree:
        blockers.append("task intake and workflow do not share one worktree")
    tool_log = run_dir / "raw-events" / "tool-calls.jsonl"
    if not tool_log.exists():
        blockers.append("tool governance audit log is missing")
    elif not tool_audit_is_allowed(tool_log):
        blockers.append("tool governance audit is malformed, empty, or contains a denied call")
    if queue_status == "completed":
        if not (
            publication.get("execution_status") == "completed"
            and publication.get("pr_created_or_updated") is True
            and publication.get("branch_pushed") is True
            and isinstance(publication.get("pr_url"), str)
            and str(publication.get("pr_url", "")).startswith("https://github.com/")
            and publication.get("worktree") == worktree
        ):
            blockers.append("completed task did not publish from its original worktree")
    elif queue_status == "blocked" and workflow.get("execution_status") not in {"blocked", "awaiting_approval"}:
        blockers.append("queue exception does not match workflow terminal state")
    return {
        "run_id": run_id,
        "queue_status": queue_status,
        "worktree": worktree,
        "real_executor": real_executor,
        "required_verifiers": sorted(required_verifiers),
        "verifiers": sorted(roles & VERIFICATION_PLANE),
        "blockers": blockers,
    }


def verify(
    *,
    runs_dir: Path,
    db_path: Path,
    minimum_tasks: int = 3,
    minimum_workers: int = 2,
) -> dict[str, Any]:
    queue = TaskQueue(db_path)
    tasks = [record for record in queue.list() if record.status in {"completed", "blocked", "dead_letter"}]
    events = queue.events()
    workers = {str(event["worker_id"]) for event in events if event["event"] == "leased" and event["worker_id"]}
    concurrency = maximum_concurrency(events)
    runs = [verify_run(runs_dir, task.run_id, task.status) for task in tasks if task.run_id]
    worktrees = [str(run["worktree"]) for run in runs if run.get("worktree")]
    isolated = len(worktrees) == len(set(worktrees)) == len(runs)
    blockers = [
        f"{run['run_id']}: {message}"
        for run in runs
        for message in run.get("blockers", [])
    ]
    if minimum_tasks < 3:
        blockers.append("Step 2 minimum_tasks cannot be lower than 3")
    if len(tasks) < minimum_tasks:
        blockers.append(f"only {len(tasks)} terminal queued tasks found; minimum is {minimum_tasks}")
    if len(workers) < minimum_workers:
        blockers.append(f"only {len(workers)} workers claimed tasks; minimum is {minimum_workers}")
    if concurrency < 2:
        blockers.append("task event timeline does not prove concurrent execution")
    if not isolated:
        blockers.append("task worktrees are missing or not isolated")
    human_exceptions = sum(task.requires_human or task.status in {"blocked", "dead_letter"} for task in tasks)
    if human_exceptions == 0:
        blockers.append("exception queue has no proven human exception")
    completed_prs = sum(task.status == "completed" for task in tasks)
    if completed_prs == 0:
        blockers.append("no concurrent task reached PR completion")
    stalled = len(queue.stalled())
    dead_letters = sum(task.status == "dead_letter" for task in tasks)
    if stalled:
        blockers.append("worker pool contains stalled tasks")
    return {
        "status": "pass" if not blockers else "fail",
        "minimum_tasks": minimum_tasks,
        "task_count": len(tasks),
        "worker_count": len(workers),
        "max_concurrency": concurrency,
        "real_executor_runs": sum(run.get("real_executor") is True for run in runs),
        "isolated_worktrees": isolated,
        "completed_prs": completed_prs,
        "human_exceptions": human_exceptions,
        "stalled_workers": stalled,
        "dead_letters": dead_letters,
        "runs": runs,
        "blockers": blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=Path(".agent-runs"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--minimum-tasks", type=int, default=3)
    parser.add_argument("--minimum-workers", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = verify(
        runs_dir=args.runs_dir,
        db_path=args.db,
        minimum_tasks=args.minimum_tasks,
        minimum_workers=args.minimum_workers,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
