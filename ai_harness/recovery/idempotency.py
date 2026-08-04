"""Read-only probes used before repeating irreversible side effects."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Sequence


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def _run(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, text=True, capture_output=True, check=False, timeout=30)


def commit_exists(repository: Path, marker: str, runner: Runner = _run) -> bool:
    result = runner(["git", "log", "--format=%B", "-n", "200"], repository)
    return result.returncode == 0 and marker in result.stdout


def branch_pushed(repository: Path, branch: str, runner: Runner = _run) -> bool:
    local = runner(["git", "rev-parse", branch], repository)
    remote = runner(["git", "ls-remote", "origin", f"refs/heads/{branch}"], repository)
    if local.returncode != 0 or remote.returncode != 0 or not remote.stdout.strip():
        return False
    return remote.stdout.split()[0] == local.stdout.strip()


def pr_exists(repository: Path, branch: str, runner: Runner = _run) -> tuple[int, str] | None:
    result = runner(["gh", "pr", "view", branch, "--json", "number,url"], repository)
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
        return int(value["number"]), str(value["url"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def approval_consumed(approval: dict[str, object]) -> bool:
    return approval.get("status") == "consumed" and int(approval.get("resume_count", 0) or 0) == 1


def artifact_written(path: Path, fingerprint: str = "") -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if not fingerprint:
        return True
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest() == fingerprint.removeprefix("sha256:")
