from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "context_compiler.py"
SPEC = importlib.util.spec_from_file_location("context_compiler", MODULE_PATH)
assert SPEC is not None
context_compiler = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = context_compiler
SPEC.loader.exec_module(context_compiler)


def test_context_manifest_references_role_scoped_skills(tmp_path: Path) -> None:
    path = context_compiler.create_context_manifest(
        run_id="run-1",
        role="planner",
        goal="Plan a task",
        repository=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        context_dir=tmp_path / "context",
        project="agent_workspace",
        project_profile="agent_workspace",
        token_budget=12000,
        allowed_tools=["filesystem_read"],
        previous_roles=["issue-intake"],
    )

    manifest = json.loads(path.read_text(encoding="utf-8"))

    skill_names = {item["name"] for item in manifest["skill_references"]}
    assert {"context-engineering", "repo-policy", "structured-output-guard"}.issubset(skill_names)


def test_context_manifest_records_role_capabilities_and_contract(tmp_path: Path) -> None:
    contract = context_compiler.role_contract("implementation-agent")
    path = context_compiler.create_context_manifest(
        run_id="run-2",
        role="implementation-agent",
        goal="Patch a task",
        repository=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        context_dir=tmp_path / "context",
        project="agent_workspace",
        project_profile="agent_workspace",
        token_budget=12000,
        allowed_tools=[],
        previous_roles=["planner"],
        filesystem_access=context_compiler.role_capability("implementation-agent")["filesystem"],
        prompt_path=contract["prompt_path"],
        output_contract=contract["output_contract"],
        expected_artifacts=contract["expected_artifacts"],
    )

    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["project_profile"] == "agent_workspace"
    assert manifest["prompt_path"] == ".agents/prompts/implementation-agent.md"
    assert manifest["output_contract"] == "schemas/role_result.schema.json"
    assert manifest["filesystem_access"] == "task_worktree_write"
    assert "apply_patch" in manifest["allowed_tools"]


def test_local_skills_have_yaml_frontmatter() -> None:
    skill_paths = sorted((Path(__file__).resolve().parents[1] / ".agents" / "skills").glob("*/SKILL.md"))

    assert skill_paths
    for path in skill_paths:
        assert path.read_text(encoding="utf-8").startswith("---\n"), path
