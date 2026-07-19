from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "project_memory.py"
SPEC = importlib.util.spec_from_file_location("project_memory", MODULE_PATH)
assert SPEC is not None
project_memory = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = project_memory
SPEC.loader.exec_module(project_memory)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_retrieval_ranks_relevant_project_memory_and_writes_provenance(tmp_path: Path) -> None:
    write(tmp_path / "docs/projects/web/privacy.md", "# Privacy\nPrivate by default.\n")
    write(
        tmp_path / "docs/projects/web/memory/topics/search.md",
        "# Search architecture\nUse a BM25 index for project memory retrieval and preserve source citations.\n",
    )
    write(
        tmp_path / "docs/projects/web/wiki/deployments.md",
        "# Deployments\nProduction deploys require a release checklist.\n",
    )
    write(
        tmp_path / "docs/projects/other/memory/topics/search.md",
        "# Other project\nBM25 private note that must remain isolated.\n",
    )
    result = project_memory.retrieve_project_memory(
        control_root=tmp_path,
        project="web",
        project_profile="nextjs_web",
        query="BM25 project memory retrieval citations",
        context_path=tmp_path / "run/context/retrieved/planner.md",
    )

    assert result.status == "retrieved"
    assert result.selected[0].chunk.display_path == "docs/projects/web/memory/topics/search.md"
    assert all("docs/projects/other" not in item.chunk.display_path for item in result.selected)
    assert result.context_path is not None
    context = result.context_path.read_text(encoding="utf-8")
    assert "Private control-plane context" in context
    assert "docs/projects/web/memory/topics/search.md" in context
    assert "source citations" in context


def test_target_project_retrieval_requires_privacy_policy(tmp_path: Path) -> None:
    write(tmp_path / "docs/projects/web/memory/MEMORY.md", "# Memory\nUnique retrieval term.\n")

    result = project_memory.retrieve_project_memory(
        control_root=tmp_path,
        project="web",
        project_profile="nextjs_web",
        query="unique retrieval term",
        context_path=tmp_path / "context.md",
    )

    assert result.status == "privacy_policy_missing"
    assert result.selected == ()
    assert not (tmp_path / "context.md").exists()


def test_retrieval_rejects_project_path_traversal(tmp_path: Path) -> None:
    write(tmp_path / "docs/projects/web/privacy.md", "# Privacy\n")

    result = project_memory.retrieve_project_memory(
        control_root=tmp_path,
        project="../web",
        project_profile="nextjs_web",
        query="privacy",
        context_path=tmp_path / "context.md",
    )

    assert result.status == "invalid_project"
    assert result.candidate_paths == ()


def test_retrieval_respects_result_and_byte_budgets(tmp_path: Path) -> None:
    write(tmp_path / "docs/projects/web/privacy.md", "# Privacy\nPrivate.\n")
    write(tmp_path / "docs/projects/web/memory/one.md", "# One\nneedle " + "a" * 80)
    write(tmp_path / "docs/projects/web/memory/two.md", "# Two\nneedle " + "b" * 80)

    result = project_memory.retrieve_project_memory(
        control_root=tmp_path,
        project="web",
        project_profile="nextjs_web",
        query="needle",
        context_path=tmp_path / "context.md",
        max_results=1,
        max_bytes=100,
    )

    assert len(result.selected) == 1
    assert len(result.selected[0].chunk.content.encode("utf-8")) <= 100


def test_agent_workspace_retrieval_uses_global_memory_only(tmp_path: Path) -> None:
    write(tmp_path / "docs/memory/topics/routing.md", "# Routing\nDeterministic routing fingerprint.\n")
    write(
        tmp_path / "docs/projects/web/memory/topics/routing.md",
        "# Private web routing\nDeterministic routing fingerprint.\n",
    )

    result = project_memory.retrieve_project_memory(
        control_root=tmp_path,
        project="agent_workspace",
        project_profile="agent_workspace",
        query="deterministic routing fingerprint",
        context_path=tmp_path / "context.md",
    )

    assert result.status == "retrieved"
    assert {item.chunk.display_path for item in result.selected} == {"docs/memory/topics/routing.md"}
