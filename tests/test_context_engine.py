from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ai_harness.context import (
    Context,
    ContextCache,
    ContextCacheKey,
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
from ai_harness.context.cache import repository_fingerprints
from ai_harness.context.sources import ObsidianSource, PolicySource
from ai_harness.context.deduplication import deduplicate_documents
from scripts.context_compiler import create_context_manifest


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


def test_policy_source_keeps_control_plane_and_target_agents_distinct(tmp_path: Path) -> None:
    control = tmp_path / "control"
    repository = tmp_path / "repository"
    prepare_control_root(control)
    write(repository / "AGENTS.md", "# Target rules\nUse target conventions.\n")
    request = KnowledgeRequest(
        "Update target code",
        repository,
        "implementation-agent",
        "runtime",
        "target",
        "agent_workspace",
    )

    documents = PolicySource(control).collect(request)
    by_path = {document.path: document for document in documents}

    assert "control-plane/AGENTS.md" in by_path
    assert "repository/AGENTS.md" in by_path
    assert "Follow repository policy" in by_path["control-plane/AGENTS.md"].content
    assert "Use target conventions" in by_path["repository/AGENTS.md"].content
    assert by_path["control-plane/AGENTS.md"].metadata["scope"] == "control_plane"
    assert by_path["repository/AGENTS.md"].metadata["scope"] == "target_repository"


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


def test_context_cache_hits_and_invalidates_only_when_selected_sources_change(tmp_path: Path) -> None:
    control = tmp_path / "control"
    repository = tmp_path / "repository"
    prepare_control_root(control)
    write(repository / "README.md", "# Project\nStable overview.\n")
    write(repository / "module.py", "VALUE = 1\n")
    engine = ContextEngine.default(
        control_root=control,
        project="example",
        project_profile="agent_workspace",
        repository=repository,
        token_budget=2000,
    )

    first = engine.build("Update project overview", repository, "planner", "runtime")
    second = engine.build("Update project overview", repository, "planner", "runtime")
    write(repository / "module.py", "VALUE = 2\n")
    compatible = engine.build("Update project overview", repository, "planner", "runtime")
    write(repository / "README.md", "# Project\nChanged authoritative overview.\n")
    rebuilt = engine.build("Update project overview", repository, "planner", "runtime")

    assert first.log["cache"]["status"] == "miss"  # type: ignore[index]
    assert second.log["cache"]["status"] == "hit"  # type: ignore[index]
    assert compatible.log["cache"]["status"] == "compatible_hit"  # type: ignore[index]
    assert rebuilt.log["cache"]["status"] == "miss"  # type: ignore[index]
    assert "Changed authoritative overview" in rebuilt.package


def test_context_cache_path_invalidation_is_selective(tmp_path: Path) -> None:
    cache = ContextCache(tmp_path / "cache")
    first_key = ContextCacheKey("head", "dirty-a", "planner", "query-a", "profile", "policy", "1")
    second_key = ContextCacheKey("head", "dirty-b", "reviewer", "query-b", "profile", "policy", "1")

    def cached(path: str) -> Context:
        return Context(
            package=f"context for {path}",
            selected=(),
            excluded=(),
            token_budget=1000,
            tokens_used=10,
            log={"selected": [{"id": path, "source": "repository_documentation", "path": path}]},
        )

    cache.put(first_key, cached("docs/one.md"), source_fingerprints={"docs/one.md": "one"})
    cache.put(second_key, cached("docs/two.md"), source_fingerprints={"docs/two.md": "two"})

    removed = cache.invalidate_paths({"docs/one.md"})

    assert removed == (first_key.digest,)
    assert cache.get(first_key) is None
    assert cache.get(second_key) is not None


def test_context_engine_deduplicates_near_identical_sources(tmp_path: Path) -> None:
    duplicate = "Policy requires bounded deterministic verification and explicit approval."
    first = KnowledgeDocument(
        id="policy",
        title="Policy",
        content=duplicate,
        source="policy",
        path="policy.md",
        knowledge_type=KnowledgeType.POLICY,
        document_type=DocumentType.POLICY,
        priority=100,
    )
    second = KnowledgeDocument(
        id="copy",
        title="Policy copy",
        content=duplicate + "\n",
        source="report",
        path="report.md",
        knowledge_type=KnowledgeType.DOCUMENTATION,
        document_type=DocumentType.PROJECT_DOC,
        priority=20,
    )

    class DuplicateSource:
        name = "duplicates"

        def collect(self, request: KnowledgeRequest) -> tuple[KnowledgeDocument, ...]:
            return first, second

    engine = ContextEngine(
        sources=(DuplicateSource(),),
        project="example",
        project_profile="agent_workspace",
        builder=ContextBuilder(ContextBudget(total_tokens=1000)),
    )

    context = engine.build("deterministic verification", tmp_path, "reviewer", "runtime")

    assert context.package.count("Policy requires bounded") == 1
    assert context.log["deduplication"]["removed_count"] == 1  # type: ignore[index]


def test_dirty_fingerprint_tracks_untracked_file_content_with_spaces(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=repository, check=True)
    write(repository / "tracked.txt", "tracked\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
    untracked = repository / "notes with spaces.md"
    write(untracked, "first\n")

    first = repository_fingerprints(repository)
    write(untracked, "second\n")
    second = repository_fingerprints(repository)

    assert first[0] == second[0]
    assert first[1] != second[1]


def test_dedup_prefers_new_authoritative_artifact_and_removes_superseded_copy() -> None:
    old = KnowledgeDocument(
        id="old-plan",
        title="Old plan",
        content="Use the old sequence.",
        source="report",
        path="report-plan.md",
        knowledge_type=KnowledgeType.DOCUMENTATION,
        document_type=DocumentType.PROJECT_DOC,
        priority=80,
    )
    current = KnowledgeDocument(
        id="current-plan",
        title="Current plan",
        content="Use the authoritative sequence.",
        source="run_artifacts",
        path="plan.md",
        knowledge_type=KnowledgeType.ARTIFACT,
        document_type=DocumentType.ARTIFACT,
        priority=90,
        metadata={"authoritative": "true", "created_at": "2026-08-26T12:00:00+00:00", "supersedes": "old-plan"},
    )

    kept, removed = deduplicate_documents((old, current))

    assert [item.id for item in kept] == ["current-plan"]
    assert [item.id for item in removed] == ["old-plan"]


def test_context_manifest_contains_real_artifact_failure_and_decision_delta(tmp_path: Path) -> None:
    control = tmp_path / "control"
    repository = tmp_path / "repository"
    run = control / ".agent-runs" / "run"
    artifacts = run / "artifacts"
    context_dir = run / "context-manifests"
    prepare_control_root(control)
    write(repository / "README.md", "# Project\n")
    write(artifacts / "plan.md", "# Plan v1\n")
    write(run / "workflow.json", json.dumps({"last_route": {"next_role": "reviewer"}}))
    write(run / "errors.jsonl", json.dumps({"code": "FIRST"}) + "\n")
    first = create_context_manifest(
        run_id="run",
        role="planner",
        goal="Update project",
        repository=repository,
        artifacts_dir=artifacts,
        context_dir=context_dir,
        project="example",
        project_profile="agent_workspace",
        token_budget=2000,
        allowed_tools=[],
        previous_roles=[],
    )
    assert first.is_file()
    write(artifacts / "plan.md", "# Plan v2\n")
    write(artifacts / "quality.json", json.dumps({"overall_status": "pass"}))
    write(run / "errors.jsonl", json.dumps({"code": "FIRST"}) + "\n" + json.dumps({"code": "SECOND"}) + "\n")

    second = create_context_manifest(
        run_id="run",
        role="reviewer",
        goal="Update project",
        repository=repository,
        artifacts_dir=artifacts,
        context_dir=context_dir,
        project="example",
        project_profile="agent_workspace",
        token_budget=2000,
        allowed_tools=[],
        previous_roles=["planner"],
    )
    manifest = json.loads(second.read_text(encoding="utf-8"))

    assert manifest["runtime_delta"]["from_role"] == "planner"
    assert set(manifest["runtime_delta"]["changed_artifacts"]) == {"plan.md", "quality.json"}
    assert len(manifest["runtime_delta"]["new_failures"]) == 1
    assert manifest["context_layers"]["delta"]
