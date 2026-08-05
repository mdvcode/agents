"""Read-only probes used before repeating irreversible side effects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Protocol, Sequence


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], Path], CommandResult]


def _run(args: Sequence[str], cwd: Path) -> CommandResult:
    import subprocess

    return subprocess.run(list(args), cwd=cwd, text=True, capture_output=True, check=False, timeout=30)


def commit_exists(repository: Path, marker: str, runner: Runner = _run) -> bool:
    return commit_sha_for_marker(repository, marker, runner) != ""


def commit_sha_for_marker(repository: Path, marker: str, runner: Runner = _run) -> str:
    if not marker:
        return ""
    result = runner(
        ["git", "log", "--format=%H%x00%B%x00", "-n", "200"],
        repository,
    )
    if result.returncode != 0:
        return ""
    fields = result.stdout.split("\x00")
    for index in range(0, len(fields) - 1, 2):
        if marker in fields[index + 1]:
            return fields[index].strip()
    return ""


def branch_pushed(repository: Path, branch: str, runner: Runner = _run) -> bool:
    local = runner(["git", "rev-parse", branch], repository)
    remote = runner(["git", "ls-remote", "origin", f"refs/heads/{branch}"], repository)
    if local.returncode != 0 or remote.returncode != 0 or not remote.stdout.strip():
        return False
    return remote.stdout.split()[0] == local.stdout.strip()


def pr_exists(
    repository: Path,
    branch: str,
    runner: Runner = _run,
    markers: Sequence[str] = (),
) -> tuple[int, str] | None:
    result = runner(["gh", "pr", "view", branch, "--json", "number,url"], repository)
    if result.returncode == 0:
        try:
            value = json.loads(result.stdout)
            return int(value["number"]), str(value["url"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    for marker in dict.fromkeys(item for item in markers if item):
        listed = runner(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--search",
                marker,
                "--json",
                "number,url,headRefName",
                "--limit",
                "20",
            ],
            repository,
        )
        if listed.returncode != 0:
            continue
        try:
            values = json.loads(listed.stdout)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(values, list):
            continue
        matching = [
            item
            for item in values
            if isinstance(item, dict) and str(item.get("headRefName", "")) == branch
        ]
        candidates = matching or [item for item in values if isinstance(item, dict)]
        if candidates:
            try:
                return int(candidates[0]["number"]), str(candidates[0]["url"])
            except (KeyError, TypeError, ValueError):
                continue
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
