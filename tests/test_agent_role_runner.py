from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent_role_runner.py"
SPEC = importlib.util.spec_from_file_location("agent_role_runner", MODULE_PATH)
assert SPEC is not None
agent_role_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = agent_role_runner
SPEC.loader.exec_module(agent_role_runner)


def fake_adapter_script(path: Path) -> str:
    path.write_text(
        """
from pathlib import Path
import json
import sys

request = json.loads(sys.stdin.read())
role = request["role"]
artifacts = Path(request["artifacts_dir"])
repository = Path(request["repository"])
if role == "planner":
    (artifacts / "plan.md").write_text("# Plan\\n", encoding="utf-8")
    created = ["plan.md"]
    next_action = "risk-classifier"
elif role == "risk-classifier":
    (artifacts / "risk.json").write_text(json.dumps({
        "risk_class": "medium",
        "reasons": [],
        "changed_areas": ["impl.txt"],
        "high_risk_triggers": [],
        "protected_paths_touched": [],
        "protected_actions_required": [],
        "autonomy_allowed": {
            "patch": True,
            "commit": True,
            "push": True,
            "open_pr": True,
            "update_pr": True,
            "auto_merge": False,
            "deploy_staging": False,
            "deploy_production": False
        }
    }), encoding="utf-8")
    created = ["risk.json"]
    next_action = "implementation-agent"
elif role == "implementation-agent":
    (repository / "impl.txt").write_text("implemented\\n", encoding="utf-8")
    created = ["impl.txt"]
    next_action = "completed"
else:
    created = []
    next_action = "completed"
print(json.dumps({
    "status": "completed",
    "next_action": next_action,
    "summary": f"{role} done",
    "artifacts_created": created,
    "blockers": [],
    "warnings": [],
    "tokens_used": 7
}))
""".lstrip(),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return f"{sys.executable} {path}"


def test_agent_role_runner_blocks_without_adapter(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    monkeypatch.delenv("AGENT_CODEX_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_LLM_COMMAND", raising=False)

    state = agent_role_runner.run_roles(run_id="run-1", artifacts_dir=tmp_path / "artifacts", dry_run=True)

    assert state["execution_status"] == "blocked"
    assert [item["role"] for item in state["roles"]] == ["issue-intake", "context-compiler", "planner"]
    assert state["roles"][-1]["result"]["summary"] == "No Codex adapter command configured."


def test_agent_role_runner_invokes_adapter_for_core_roles(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    command = fake_adapter_script(tmp_path / "fake_adapter.py")

    state = agent_role_runner.run_roles(
        run_id="run-2",
        artifacts_dir=tmp_path / "artifacts",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )

    assert state["execution_status"] == "completed"
    assert [item["role"] for item in state["roles"]] == [
        "issue-intake",
        "context-compiler",
        "planner",
        "risk-classifier",
        "implementation-agent",
    ]
    assert (tmp_path / "artifacts" / "planner.json").exists()
    assert (tmp_path / "artifacts" / "risk.json").exists()
    assert (tmp_path / "impl.txt").read_text(encoding="utf-8") == "implemented\n"
    assert (tmp_path / ".agent-runs" / "run-2" / "requests" / "planner.json").exists()
    assert (tmp_path / ".agent-runs" / "run-2" / "context" / "planner.json").exists()
    request = json.loads((tmp_path / ".agent-runs" / "run-2" / "requests" / "planner.json").read_text(encoding="utf-8"))
    assert request["prompt_path"] == ".agents/prompts/planner.md"
    assert request["output_contract"] == "schemas/roles/planner.schema.json"
    assert request["project_profile"] == "agent_workspace"
    assert request["expected_artifacts"] == ["plan.md"]
    assert request["filesystem_access"] == "read_only"
    assert request["allowed_tools"] == ["filesystem_read", "repository_search"]


def test_implementation_artifact_validation_detects_source_repo_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    source.mkdir()
    worktree.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
    (source / "file.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=source, check=True, capture_output=True, text=True)
    before = agent_role_runner.git_snapshot(source)
    (source / "file.txt").write_text("after\n", encoding="utf-8")

    errors = agent_role_runner.validate_role_artifacts(
        role="implementation-agent",
        result={
            "status": "completed",
            "next_action": "continue",
            "summary": "done",
            "artifacts_created": [],
            "blockers": [],
            "warnings": [],
            "tokens_used": 1,
        },
        artifacts_dir=tmp_path / "artifacts",
        worktree=worktree,
        source_repository=source,
        source_snapshot_before=before,
        create_task_worktree=True,
    )

    assert "implementation-agent changed the source repository instead of only the task worktree" in errors
