from __future__ import annotations

from pathlib import Path

import pytest

from ai_harness.recovery.checkpoints import (
    CheckpointError,
    RoleCheckpoint,
    read_checkpoint,
    resume_operation,
    write_checkpoint,
)


@pytest.mark.parametrize(
    ("state", "operation"),
    [
        ("role_pending", "execute_role"),
        ("role_running", "execute_role"),
        ("role_output_received", "validate_output"),
        ("role_validating", "validate_output"),
        ("role_completed", "next_role"),
    ],
)
def test_resume_uses_last_safe_checkpoint(state: str, operation: str, tmp_path: Path) -> None:
    checkpoint = RoleCheckpoint("run-1", "implementation-agent", state, 2, str(tmp_path / "worktree"))
    write_checkpoint(tmp_path, checkpoint)
    loaded = read_checkpoint(tmp_path, "implementation-agent")
    assert loaded is not None
    assert loaded.run_id == "run-1"
    assert loaded.worktree == str(tmp_path / "worktree")
    assert resume_operation(loaded) == operation


def test_corrupted_checkpoint_is_controlled_error(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints" / "implementation-agent.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(CheckpointError, match="corrupted checkpoint"):
        read_checkpoint(tmp_path, "implementation-agent")
