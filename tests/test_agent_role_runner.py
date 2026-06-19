from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent_role_runner.py"
SPEC = importlib.util.spec_from_file_location("agent_role_runner", MODULE_PATH)
assert SPEC is not None
agent_role_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = agent_role_runner
SPEC.loader.exec_module(agent_role_runner)


def test_agent_role_runner_writes_run_scoped_artifacts(tmp_path: Path, monkeypatch: object) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for prompt_file in set(agent_role_runner.PROMPT_FILES.values()):
        if prompt_file:
            (prompts / prompt_file).write_text("# Prompt\n", encoding="utf-8")
    monkeypatch.setattr(agent_role_runner, "PROMPTS", prompts)
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")

    state = agent_role_runner.run_roles(run_id="run-1", artifacts_dir=tmp_path / "artifacts", dry_run=True)

    assert state["execution_status"] == "completed"
    assert (tmp_path / "artifacts" / "planner.json").exists()
    assert (tmp_path / ".agent-runs" / "run-1" / "agent_workflow.json").exists()
    assert len(state["roles"]) == len(agent_role_runner.ROLE_CHAIN)
