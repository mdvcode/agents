from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_harness.context import (
    ContextBudget,
    ContextBuilder,
    ContextEngine,
    DocumentType,
    KnowledgeDocument,
    KnowledgeRequest,
    KnowledgeType,
    MemoryManager,
    RetrievalResult,
    RetrievedDocument,
    RuleBasedRetriever,
    estimate_tokens,
)
from ai_harness.context.sources import ObsidianSource


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def prepare_control_root(root: Path) -> None:
    write(root / "AGENTS.md", "# Rules\nFollow repository policy and security rules.\n")
    write(root / ".agent-policy.yaml", "version: 1\nsecurity: strict\n")
    write(root / ".agent-tool-policy.yaml", "version: 1\ntools: {}\n")
    write(root / ".agent-project-profiles.yaml", "version: 1\nprofiles:\n  django: {}\n")
    write(
        root / ".agent-role-contracts.yaml",
        "version: 1\ndefault: {}\nroles:\n  planner:\n    expected_artifacts: [plan.md]\n",
    )
    write(root / ".agent-role-capabilities.yaml", "version: 1\nroles:\n  planner:\n    tools: [filesystem_read]\n")
    write(root / ".agent-artifact-owners.yaml", "version: 1\nartifacts:\n  plan.md: planner\n")
    write(
        root / ".agents/skills/security-checklist/SKILL.md",
        "---\nname: security-checklist\n---\n# Security\nReview OAuth authentication and token handling.\n",
    )
    write(
        root / ".agents/skills/context-engineering/SKILL.md",
        "---\nname: context-engineering\n---\n# Context\nRetrieve only relevant project knowledge.\n",
    )


def test_context_engine_routes_oauth_to_adr_security_skill_and_obsidian(tmp_path: Path) -> None:
    control = tmp_path / "control"
    repository = tmp_path / "repository"
    prepare_control_root(control)
    write(repository / "README.md", "# Web\nWeb service overview.\n")
    write(
        repository / "docs/adr/0001-oauth.md",
        "# OAuth ADR\nUse OIDC authorization code flow and validate tokens.\n",
    )
    (repository / ".obsidian").mkdir(parents=True)
    write(
        repository / "Security Notes.md",
        "# Authentication\nOAuth token rotation is required by the security design.\n",
    )
    log_path = tmp_path / "run/context-logs/planner.jsonl"
    engine = ContextEngine.default(
        control_root=control,
        project="web",
        project_profile="django",
        context_log_path=log_path,
        repository=repository,
        token_budget=4000,
    )

    context = engine.build("Implement OAuth login", repository, "planner", "codex-cli")

    assert "Use OIDC authorization code flow" in context.package
    assert "OAuth token rotation" in context.package
    assert "Review OAuth authentication" in context.package
    assert estimate_tokens(context.package) <= 4000
    assert context.log_path == str(log_path.resolve())
    event = json.loads(log_path.read_text(encoding="utf-8"))
    assert event["retriever"] == "rule_based_keyword_v1"
    assert {item["source"] for item in event["selected"]} >= {
        "repository_documentation",
        "skills",
        "obsidian",
        "policies",
    }


def test_context_builder_enforces_total_and_category_budgets() -> None:
    request = KnowledgeRequest(
        task="Explain OAuth architecture",
        repository=Path("/tmp/example"),
        role="planner",
        runtime="test-runtime",
        project="example",
        project_profile="django",
    )
    documents = tuple(
        KnowledgeDocument(
            id=str(index),
            title=f"ADR {index}",
            content="oauth architecture " + ("detail " * 1000),
            source="test",
            path=f"docs/adr/{index}.md",
            knowledge_type=KnowledgeType.DOCUMENTATION,
            document_type=DocumentType.ADR,
            priority=70,
        )
        for index in range(3)
    )
    retrieval = RuleBasedRetriever().retrieve(request, documents)
    context = ContextBuilder(ContextBudget(total_tokens=1000)).build(request, retrieval)

    assert estimate_tokens(context.package) <= 1000
    assert context.tokens_used <= 1000
    assert context.selected
    assert any(item.truncated for item in context.selected)
    assert context.excluded


def test_obsidian_source_ignores_symlinks_and_paths_outside_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside.md"
    (vault / ".obsidian").mkdir(parents=True)
    write(vault / "safe.md", "# Safe\nOAuth project note.\n")
    write(outside, "# Private\nMust not be followed.\n")
    os.symlink(outside, vault / "linked.md")
    request = KnowledgeRequest("OAuth", vault, "planner", "test", "vault", "agent_workspace")

    documents = ObsidianSource.discover(vault).collect(request)

    assert {item.path for item in documents} == {"safe.md"}
    assert all("Must not be followed" not in item.content for item in documents)


def test_context_engine_accepts_alternate_retriever_without_api_change(tmp_path: Path) -> None:
    document = KnowledgeDocument(
        id="one",
        title="Selected",
        content="alternate backend result",
        source="fixture",
        path="fixture.md",
        knowledge_type=KnowledgeType.DOCUMENTATION,
        document_type=DocumentType.PROJECT_DOC,
        priority=30,
    )

    class Source:
        name = "fixture"

        def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
            return (document,)

    class AlternateRetriever:
        name = "future_semantic_backend"

        def retrieve(
            self,
            request: KnowledgeRequest,
            documents: tuple[KnowledgeDocument, ...],
        ) -> RetrievalResult:
            return RetrievalResult(
                selected=(RetrievedDocument(documents[0], 1.0, "alternate_backend"),),
                excluded=(),
                query_terms=("future",),
                algorithm=self.name,
            )

    engine = ContextEngine(
        sources=(Source(),),
        project="example",
        project_profile="agent_workspace",
        retriever=AlternateRetriever(),
        builder=ContextBuilder(ContextBudget(total_tokens=2000)),
    )

    context = engine.build("Future retrieval", tmp_path, "planner", "runtime")

    assert "alternate backend result" in context.package
    assert context.log["retriever"] == "future_semantic_backend"


def test_memory_manager_is_interface_only() -> None:
    with pytest.raises(TypeError):
        MemoryManager()
