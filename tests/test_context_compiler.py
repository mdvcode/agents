from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from ai_harness.project import trust_key


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
    assert manifest["context_budget"]["max_total_bytes"] == 120000
    assert manifest["context_budget"]["max_file_bytes"] == 24000
    assert manifest["context_budget"]["max_total_tokens"] == 12000
    assert manifest["context_budget"]["used_tokens"] <= 12000
    assert isinstance(manifest["selected_context"], list)
    assert isinstance(manifest["excluded_context"], list)
    assert manifest["retrieval_queries"] == ["Plan a task planner"]
    assert isinstance(manifest["source_file_candidates"], list)
    assert manifest["repo_intelligence"]["context_engine"]["algorithm"] == "rule_based_keyword_v1"
    assert Path(manifest["context_package_path"]).is_file()
    assert Path(manifest["context_log_path"]).is_file()


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


def test_context_manifest_includes_retrieved_project_knowledge(tmp_path: Path, monkeypatch: object) -> None:
    control_root = tmp_path / "control"
    privacy = control_root / "docs/projects/web/privacy.md"
    knowledge = control_root / "docs/projects/web/wiki/search.md"
    privacy.parent.mkdir(parents=True)
    privacy.write_text("# Privacy\nPrivate by default.\n", encoding="utf-8")
    knowledge.parent.mkdir(parents=True)
    knowledge.write_text(
        "# Search\nRule-based project knowledge retrieval keeps provenance.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(context_compiler, "MEMORY_CONTROL_ROOT", control_root)

    path = context_compiler.create_context_manifest(
        run_id="run-rag",
        role="planner",
        goal="Implement rule-based project knowledge retrieval",
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
    assert manifest["context_files"] == [
        {"path": manifest["context_package_path"], "kind": "context_package"}
    ]
    assert any(
        item["source"] == "private_project_knowledge"
        and item["path"] == "web/wiki/search.md"
        for item in manifest["selected_context"]
    )
    package = Path(manifest["context_package_path"]).read_text(encoding="utf-8")
    assert "Rule-based project knowledge retrieval keeps provenance" in package
    assert manifest["repo_intelligence"]["context_engine"]["status"] == "compiled"


def test_project_key_prevents_duplicate_display_id_context_leak(
    tmp_path: Path, monkeypatch: object
) -> None:
    control_root = tmp_path / "control"
    first_repository = tmp_path / "repo-a"
    second_repository = tmp_path / "repo-b"
    first_repository.mkdir()
    second_repository.mkdir()
    first_key = trust_key(first_repository)
    second_key = trust_key(second_repository)
    legacy = control_root / "docs/projects/shared/wiki/legacy.md"
    keyed_root = control_root / "docs/projects/by-key" / first_key
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# Legacy secret\nMust not cross repositories.\n", encoding="utf-8")
    (keyed_root / "wiki").mkdir(parents=True)
    (keyed_root / "privacy.md").write_text("# Private\n", encoding="utf-8")
    (keyed_root / "wiki/context.md").write_text(
        "# Bound context\nRepository-bound planning context.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(context_compiler, "MEMORY_CONTROL_ROOT", control_root)

    first = context_compiler.create_context_manifest(
        run_id="run-keyed-a",
        role="planner",
        goal="Use repository-bound planning context",
        repository=first_repository,
        artifacts_dir=tmp_path / "artifacts-a",
        context_dir=tmp_path / "context-a",
        project="shared",
        project_key=first_key,
        project_profile="nextjs_web",
        token_budget=12000,
        allowed_tools=["filesystem_read"],
        previous_roles=[],
    )
    second = context_compiler.create_context_manifest(
        run_id="run-keyed-b",
        role="planner",
        goal="Use repository-bound planning context",
        repository=second_repository,
        artifacts_dir=tmp_path / "artifacts-b",
        context_dir=tmp_path / "context-b",
        project="shared",
        project_key=second_key,
        project_profile="nextjs_web",
        token_budget=12000,
        allowed_tools=["filesystem_read"],
        previous_roles=[],
    )

    first_manifest = json.loads(first.read_text(encoding="utf-8"))
    second_manifest = json.loads(second.read_text(encoding="utf-8"))
    first_package = Path(first_manifest["context_package_path"]).read_text(encoding="utf-8")
    second_package = Path(second_manifest["context_package_path"]).read_text(encoding="utf-8")
    assert first_manifest["project_key"] == first_key
    assert "Repository-bound planning context" in first_package
    assert "Must not cross repositories" not in first_package
    assert "Repository-bound planning context" not in second_package
    assert "Must not cross repositories" not in second_package


def test_local_skills_have_yaml_frontmatter() -> None:
    skill_paths = sorted((Path(__file__).resolve().parents[1] / ".agents" / "skills").glob("*/SKILL.md"))

    assert skill_paths
    for path in skill_paths:
        assert path.read_text(encoding="utf-8").startswith("---\n"), path
