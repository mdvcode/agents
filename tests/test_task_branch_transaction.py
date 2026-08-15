from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from worktree_manager import prepare_task_branch, rollback_prepared_task_branch


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)


def current_branch(path: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_new_task_never_reuses_an_existing_branch(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    subprocess.run(["git", "branch", "feat/existing"], cwd=tmp_path, check=True)

    result = prepare_task_branch(tmp_path, "feat/existing", "main")

    assert result["execution_status"] == "blocked"
    assert result["created_branch"] is False
    assert "refusing to reuse" in " ".join(result["errors"])
    assert current_branch(tmp_path) == "main"


def test_fresh_task_branch_can_be_rolled_back_before_queue_commit(tmp_path: Path) -> None:
    initialize_repository(tmp_path)

    prepared = prepare_task_branch(tmp_path, "feat/transaction", "main")
    errors = rollback_prepared_task_branch(tmp_path, prepared)

    assert prepared["execution_status"] == "completed"
    assert prepared["created_branch"] is True
    assert errors == []
    assert current_branch(tmp_path) == "main"
    assert subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/feat/transaction"],
        cwd=tmp_path,
        capture_output=True,
    ).returncode != 0


def test_rollback_refuses_to_delete_a_branch_after_head_moves(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    prepared = prepare_task_branch(tmp_path, "feat/changed", "main")
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "change.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=tmp_path, check=True, capture_output=True)

    errors = rollback_prepared_task_branch(tmp_path, prepared)

    assert any("HEAD changed" in error for error in errors)
    assert current_branch(tmp_path) == "feat/changed"
