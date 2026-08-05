from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ai_harness.recovery.checkpoints import (
    CheckpointError,
    RoleCheckpoint,
    read_checkpoint,
    resume_operation,
    write_checkpoint,
)


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import agent_role_runner  # noqa: E402


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


def test_corrupted_checkpoint_routes_existing_run_to_controlled_dead_letter(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    runs = tmp_path / ".agent-runs"
    run_dir = runs / "run-corrupt"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (run_dir / "checkpoints").mkdir(parents=True)
    (run_dir / "checkpoints" / "planner.json").write_text("{bad", encoding="utf-8")
    workflow = {
        "run_id": "run-corrupt",
        "workflow": "full_agent_workflow",
        "task_id": "task-corrupt",
        "goal": "test corrupted checkpoint",
        "project": "agent_workspace",
        "project_profile": "agent_workspace",
        "repository": str(tmp_path),
        "worktree": str(worktree),
        "branch": "feat/corrupt",
        "base_branch": "main",
        "execution_status": "resuming",
        "resume_role": "planner",
        "roles": [],
        "tokens_used": 0,
        "input_fingerprint": "sha256:test",
        "runtime": {
            "provider": "test-subprocess",
            "kind": "runtime_adapter",
            "transport": "test_fixture",
            "production": False,
            "command": f"{sys.executable} -c 'print(1)'",
            "api_required": False,
        },
    }
    (run_dir / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    monkeypatch.setattr(agent_role_runner, "RUNS", runs)

    result = agent_role_runner.run_roles(run_id="run-corrupt", resume=True)

    assert result["execution_status"] == "dead_letter"
    assert result["failure_kind"] == "unrecoverable"
    assert result["recovery_action"] == "dead_letter"
    failures = list((run_dir / "failures").glob("*.json"))
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert failure["error_type"] == "CorruptedCheckpoint"
