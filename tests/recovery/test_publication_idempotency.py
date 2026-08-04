from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ai_harness.recovery.idempotency import approval_consumed, branch_pushed, commit_exists, pr_exists


def runner(args: list[str] | tuple[str, ...], _cwd: Path) -> subprocess.CompletedProcess[str]:
    command = list(args)
    if command[:2] == ["git", "log"]:
        return subprocess.CompletedProcess(command, 0, "run-1:publication\n", "")
    if command[:2] == ["git", "rev-parse"]:
        return subprocess.CompletedProcess(command, 0, "abc123\n", "")
    if command[:2] == ["git", "ls-remote"]:
        return subprocess.CompletedProcess(command, 0, "abc123\trefs/heads/issue/task\n", "")
    if command[:3] == ["gh", "pr", "view"]:
        return subprocess.CompletedProcess(command, 0, json.dumps({"number": 42, "url": "https://example/pr/42"}), "")
    return subprocess.CompletedProcess(command, 1, "", "unknown")


def test_commit_push_and_pr_probes_prevent_duplicate_side_effects(tmp_path: Path) -> None:
    assert commit_exists(tmp_path, "run-1:publication", runner)
    assert branch_pushed(tmp_path, "issue/task", runner)
    assert pr_exists(tmp_path, "issue/task", runner) == (42, "https://example/pr/42")


def test_approval_grant_is_consumed_exactly_once() -> None:
    assert approval_consumed({"status": "consumed", "resume_count": 1}) is True
    assert approval_consumed({"status": "consumed", "resume_count": 2}) is False
