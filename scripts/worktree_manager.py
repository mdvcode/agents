#!/usr/bin/env python3
"""Prepare and record task-scoped Git branches or worktrees."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Collection


ROOT = Path(__file__).resolve().parents[1]
WORKTREES = ROOT / ".agent-worktrees"
RUNS = ROOT / ".agent-runs"


def slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_./" else "-" for char in value).strip("-./") or "task"


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def inspect_current_checkout(
    repo: Path,
    *,
    expected_branch: str = "",
    protected_branches: Collection[str] = (),
    require_clean: bool = True,
) -> dict[str, object]:
    repo = repo.resolve()
    errors: list[str] = []
    branch_result = run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    if not branch:
        errors.append("current checkout must be on a branch; detached HEAD is unsupported")
    elif expected_branch and branch != expected_branch:
        errors.append(f"current branch changed before execution: expected {expected_branch!r}, found {branch!r}")
    elif branch in set(protected_branches):
        errors.append(f"current branch {branch!r} is protected or configured as the default branch")

    status = run_git(repo, ["status", "--porcelain=v1", "--untracked-files=normal"])
    dirty_paths = [line[3:] if len(line) > 3 else line for line in status.stdout.splitlines() if line]
    if status.returncode != 0:
        errors.append(status.stderr.strip() or "cannot inspect current checkout status")
    elif require_clean and dirty_paths:
        preview = ", ".join(dirty_paths[:5])
        suffix = " ..." if len(dirty_paths) > 5 else ""
        errors.append(
            "current checkout has uncommitted changes; commit or stash them before queueing the task"
            + (f": {preview}{suffix}" if preview else "")
        )
    return {
        "repository": str(repo),
        "branch": branch,
        "dirty_paths": dirty_paths,
        "errors": errors,
    }


def use_current_checkout(
    repo: Path,
    task_id: str,
    branch: str,
    base_branch: str,
    run_id: str,
    *,
    runs_dir: Path = RUNS,
    require_clean: bool = True,
) -> dict[str, object]:
    repo = repo.resolve()
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    checkout = inspect_current_checkout(
        repo,
        expected_branch=branch,
        protected_branches={base_branch, "main", "master", "trunk"},
        require_clean=require_clean,
    )
    errors = [str(item) for item in checkout["errors"]]
    result = {
        "run_id": run_id,
        "task_id": task_id,
        "repository": str(repo),
        "branch": branch,
        "base_branch": base_branch,
        "worktree": str(repo),
        "workspace_mode": "current_branch",
        "execution_status": "blocked" if errors else "completed",
        "errors": errors,
        "warnings": [],
    }
    (run_dir / "worktree.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def resolve_base_ref(repo: Path, base_branch: str) -> tuple[str, list[str]]:
    """Fetch when possible and select an existing remote or local base ref."""

    warnings: list[str] = []
    fetch = run_git(repo, ["fetch", "--prune", "origin"])
    if fetch.returncode != 0:
        warnings.append("git fetch was unavailable; using an existing local reference")
    origin_ref = f"origin/{base_branch}"
    if run_git(repo, ["rev-parse", "--verify", origin_ref]).returncode == 0:
        return origin_ref, warnings
    if run_git(repo, ["rev-parse", "--verify", base_branch]).returncode == 0:
        warnings.append(f"origin/{base_branch} does not exist; using local branch {base_branch}")
        return base_branch, warnings
    return "", warnings


def prepare_task_branch(
    repo: Path,
    branch: str,
    base_branch: str,
) -> dict[str, object]:
    """Create or select one task branch in the current clean checkout."""

    repo = repo.resolve()
    checkout = inspect_current_checkout(repo, require_clean=True)
    errors = [str(item) for item in checkout["errors"]]
    warnings: list[str] = []
    branch_check = run_git(repo, ["check-ref-format", "--branch", branch])
    if branch_check.returncode != 0:
        errors.append(branch_check.stderr.strip() or branch_check.stdout.strip() or "invalid task branch")
    if errors:
        return {
            "repository": str(repo),
            "branch": branch,
            "base_branch": base_branch,
            "workspace_mode": "current_branch",
            "execution_status": "blocked",
            "errors": errors,
            "warnings": warnings,
        }
    current_branch = str(checkout["branch"])
    if current_branch != branch:
        base_ref, warnings = resolve_base_ref(repo, base_branch)
        if not base_ref:
            errors.append(
                f"base branch {base_branch} does not exist locally or as origin/{base_branch}"
            )
        else:
            branch_exists = run_git(repo, ["show-ref", "--verify", f"refs/heads/{branch}"]).returncode == 0
            arguments = ["switch", branch] if branch_exists else ["switch", "-c", branch, base_ref]
            switched = run_git(repo, arguments)
            if switched.returncode != 0:
                errors.append(switched.stderr.strip() or switched.stdout.strip() or "cannot switch task branch")
    if not errors:
        verified = inspect_current_checkout(
            repo,
            expected_branch=branch,
            protected_branches={base_branch, "main", "master", "trunk"},
            require_clean=True,
        )
        errors.extend(str(item) for item in verified["errors"])
    return {
        "repository": str(repo),
        "branch": branch,
        "base_branch": base_branch,
        "workspace_mode": "current_branch",
        "execution_status": "blocked" if errors else "completed",
        "errors": errors,
        "warnings": warnings,
    }


def create_worktree(
    repo: Path,
    task_id: str,
    branch: str,
    base_branch: str,
    run_id: str = "",
    runs_dir: Path = RUNS,
    worktrees_dir: Path = WORKTREES,
) -> dict[str, object]:
    repo = repo.resolve()
    run_id = run_id or datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S.%fZ-{slug(task_id)}")
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    worktree = worktrees_dir / f"{slug(task_id)}-{run_id.rsplit('-', 1)[-1]}"
    result = {
        "run_id": run_id,
        "task_id": task_id,
        "repository": str(repo),
        "branch": branch,
        "base_branch": base_branch,
        "worktree": str(worktree.resolve()),
        "execution_status": "planned",
        "errors": [],
        "warnings": [],
    }
    base_ref, warnings = resolve_base_ref(repo, base_branch)
    result["warnings"] = warnings
    if not base_ref:
        result["execution_status"] = "blocked"
        result["errors"] = [
            f"base branch {base_branch} does not exist locally or as origin/{base_branch}"
        ]
    elif not worktree.exists():
        branch_exists = run_git(repo, ["show-ref", "--verify", f"refs/heads/{branch}"]).returncode == 0
        args = ["worktree", "add", str(worktree), branch]
        if not branch_exists:
            args = ["worktree", "add", "-b", branch, str(worktree), base_ref]
        add = run_git(repo, args)
        if add.returncode != 0:
            result["execution_status"] = "failed"
            result["errors"] = [add.stderr.strip() or add.stdout.strip()]
        else:
            result["execution_status"] = "completed"
    else:
        result["execution_status"] = "completed"
    (run_dir / "worktree.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--run-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = create_worktree(args.repo, args.task_id, args.branch, args.base_branch, args.run_id)
    return 0 if result["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
