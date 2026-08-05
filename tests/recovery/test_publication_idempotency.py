from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ai_harness.recovery.idempotency import approval_consumed, branch_pushed, commit_exists, pr_exists


def runner(args: list[str] | tuple[str, ...], _cwd: Path) -> subprocess.CompletedProcess[str]:
    command = list(args)
    if command[:2] == ["git", "log"]:
        return subprocess.CompletedProcess(command, 0, "abc123\x00subject\n\nrun-1:publication\x00", "")
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


def test_pr_probe_falls_back_to_run_marker_and_idempotency_key(tmp_path: Path) -> None:
    def marker_runner(args: list[str] | tuple[str, ...], _cwd: Path) -> subprocess.CompletedProcess[str]:
        command = list(args)
        if command[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[:3] == ["gh", "pr", "list"] and "run-1:publication" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps([{"number": 43, "url": "https://example/pr/43", "headRefName": "issue/task"}]),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")

    assert pr_exists(
        tmp_path,
        "issue/task",
        marker_runner,
        markers=("run-1", "run-1:publication"),
    ) == (43, "https://example/pr/43")


def test_approval_grant_is_consumed_exactly_once() -> None:
    assert approval_consumed({"status": "consumed", "resume_count": 1}) is True
    assert approval_consumed({"status": "consumed", "resume_count": 2}) is False
