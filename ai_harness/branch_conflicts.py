"""Deterministic overlap analysis for concurrent task branches."""

from __future__ import annotations

import subprocess
from itertools import combinations
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"completed", "cancelled", "dead_letter", "failed"}


def checkout_changed_paths(checkout: Path) -> set[str]:
    if not checkout.is_dir():
        return set()
    paths: set[str] = set()
    for command in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            result = subprocess.run(
                command,
                cwd=checkout,
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            paths.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return paths


def analyze_branch_conflicts(
    queue_items: list[dict[str, Any]],
    runs_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates_by_identity: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for task in queue_items:
        if str(task.get("status", "")) in TERMINAL_STATUSES:
            continue
        payload = task.get("payload", {})
        if not isinstance(payload, dict):
            continue
        run = runs_by_id.get(str(task.get("run_id", "")), {})
        repository = str(payload.get("repository") or run.get("repository") or "")
        branch = str(payload.get("task_branch") or payload.get("branch") or run.get("branch") or "")
        checkout = str(run.get("checkout_path") or payload.get("checkout_path") or "")
        if not repository or not branch or not checkout:
            continue
        run_id = str(task.get("run_id", ""))
        task_id = str(payload.get("task_id", ""))
        identity = (repository, run_id or task_id or branch, branch, checkout)
        queue_task_id = int(task.get("id", 0) or 0)
        existing = candidates_by_identity.get(identity)
        if existing is not None:
            existing["queue_task_id"] = min(existing["queue_task_id"], queue_task_id)
            continue
        candidates_by_identity[identity] = {
            "queue_task_id": queue_task_id,
            "run_id": run_id,
            "task_id": task_id,
            "repository": repository,
            "branch": branch,
            "paths": checkout_changed_paths(Path(checkout)),
        }
    candidates = list(candidates_by_identity.values())
    conflicts: list[dict[str, Any]] = []
    conflict_pairs: set[tuple[str, str, str]] = set()
    for left, right in combinations(candidates, 2):
        if left["repository"] != right["repository"] or left["branch"] == right["branch"]:
            continue
        overlap = sorted(left["paths"] & right["paths"])
        if not overlap:
            continue
        pair_key = (left["repository"], *sorted((left["branch"], right["branch"])))
        if pair_key in conflict_pairs:
            continue
        conflict_pairs.add(pair_key)
        first, second = sorted((left, right), key=lambda item: item["queue_task_id"])
        conflicts.append(
            {
                "repository": left["repository"],
                "run_ids": [left["run_id"], right["run_id"]],
                "task_ids": [left["task_id"], right["task_id"]],
                "branches": [left["branch"], right["branch"]],
                "overlapping_paths": overlap[:20],
                "overlap_count": len(overlap),
                "recommended_first_run_id": first["run_id"],
                "recommended_rebase_run_id": second["run_id"],
                "recommendation": (
                    f"publish {first['branch']} first, then rebase {second['branch']} "
                    "before its final verification"
                ),
            }
        )
    return conflicts
