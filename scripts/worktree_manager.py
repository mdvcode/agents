#!/usr/bin/env python3
"""Create and record task-scoped git worktrees."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKTREES = ROOT / ".agent-worktrees"
RUNS = ROOT / ".agent-runs"


def slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_./" else "-" for char in value).strip("-./") or "task"


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)


def create_worktree(repo: Path, task_id: str, branch: str, base_branch: str, run_id: str = "") -> dict[str, object]:
    repo = repo.resolve()
    run_id = run_id or datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S.%fZ-{slug(task_id)}")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    worktree = WORKTREES / f"{slug(task_id)}-{run_id.rsplit('-', 1)[-1]}"
    result = {
        "run_id": run_id,
        "task_id": task_id,
        "repository": str(repo),
        "branch": branch,
        "base_branch": base_branch,
        "worktree": str(worktree.resolve()),
        "execution_status": "planned",
        "errors": [],
    }
    fetch = run_git(repo, ["fetch", "--prune", "origin"])
    if fetch.returncode != 0:
        result["execution_status"] = "blocked"
        result["errors"] = [fetch.stderr.strip() or fetch.stdout.strip()]
    else:
        verify = run_git(repo, ["rev-parse", "--verify", f"origin/{base_branch}"])
        if verify.returncode != 0:
            result["execution_status"] = "blocked"
            result["errors"] = [f"base branch origin/{base_branch} does not exist"]
        elif not worktree.exists():
            branch_exists = run_git(repo, ["show-ref", "--verify", f"refs/heads/{branch}"]).returncode == 0
            args = ["worktree", "add", str(worktree), branch]
            if not branch_exists:
                args = ["worktree", "add", "-b", branch, str(worktree), f"origin/{base_branch}"]
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
