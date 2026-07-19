from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "context_compiler.py"
sys.path.insert(0, str(MODULE_PATH.parent))
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
    assert manifest["context_budget"] == {"max_total_bytes": 120000, "max_file_bytes": 24000}
    assert isinstance(manifest["selected_context"], list)
    assert manifest["excluded_context"] == []
    assert manifest["retrieval_queries"] == ["Plan a task planner"]
    assert isinstance(manifest["source_file_candidates"], list)
    assert manifest["repo_intelligence"]["project_memory_retrieval"]["algorithm"] == "bm25_markdown_sections"


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


def test_nextjs_web_implementation_context_omits_python_standards(tmp_path: Path) -> None:
    path = context_compiler.create_context_manifest(
        run_id="run-3",
        role="implementation-agent",
        goal="Patch Next.js web",
        repository=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        context_dir=tmp_path / "context",
        project="nextjs_web",
        project_profile="nextjs_web",
        token_budget=12000,
        allowed_tools=[],
        previous_roles=["planner"],
    )

    manifest = json.loads(path.read_text(encoding="utf-8"))

    skill_names = {item["name"] for item in manifest["skill_references"]}
    assert "python-standards" not in skill_names
    assert "test-writing" in skill_names


def test_context_manifest_includes_retrieved_project_memory(tmp_path: Path, monkeypatch: object) -> None:
    control_root = tmp_path / "control"
    privacy = control_root / "docs/projects/web/privacy.md"
    memory = control_root / "docs/projects/web/memory/topics/search.md"
    privacy.parent.mkdir(parents=True)
    privacy.write_text("# Privacy\nPrivate by default.\n", encoding="utf-8")
    memory.parent.mkdir(parents=True)
    memory.write_text(
        "# Search\nBM25 project memory retrieval keeps provenance.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(context_compiler, "MEMORY_CONTROL_ROOT", control_root)

    path = context_compiler.create_context_manifest(
        run_id="run-rag",
        role="planner",
        goal="Implement BM25 project memory retrieval",
        repository=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        context_dir=tmp_path / "context",
        project="web",
        project_profile="nextjs_web",
        token_budget=12000,
        allowed_tools=["filesystem_read"],
        previous_roles=["context-compiler"],
    )

    manifest = json.loads(path.read_text(encoding="utf-8"))
    retrieved = [item for item in manifest["context_files"] if item["kind"] == "retrieved_project_memory"]

    assert len(retrieved) == 1
    assert Path(retrieved[0]["path"]).is_file()
    assert manifest["selected_context"][0]["path"] == "docs/projects/web/memory/topics/search.md"
    assert manifest["repo_intelligence"]["project_memory_retrieval"]["status"] == "retrieved"
    assert any(item["kind"] == "project_privacy" for item in manifest["context_files"])


def test_local_skills_have_yaml_frontmatter() -> None:
    skill_paths = sorted((Path(__file__).resolve().parents[1] / ".agents" / "skills").glob("*/SKILL.md"))

    assert skill_paths
    for path in skill_paths:
        assert path.read_text(encoding="utf-8").startswith("---\n"), path
