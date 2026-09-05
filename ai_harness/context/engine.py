"""Single entry point for knowledge discovery and role-context compilation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ai_harness.project import trust_key

from .builder import ContextBudget, ContextBuilder
from .cache import (
    ContextCache,
    ContextCacheKey,
    document_fingerprints,
    document_revision,
    fingerprint_text,
    repository_fingerprints,
    version_fingerprint,
)
from .deduplication import deduplicate_documents
from .logging import ContextLogger, JsonlContextLogger, attach_log
from .models import Context, KnowledgeDocument, KnowledgeRequest
from .retrieval import Retriever, RuleBasedRetriever
from .sources import (
    ArtifactSource,
    ContractSource,
    KnowledgeSource,
    ObsidianSource,
    PolicySource,
    PrivateProjectKnowledgeSource,
    ProjectProfileSource,
    RepositoryDocumentationSource,
    RepositoryMetadataSource,
    RuntimeDeltaSource,
    SkillSource,
)


def _task_text(task: object) -> str:
    if isinstance(task, str):
        return task.strip()
    if isinstance(task, Mapping):
        return json.dumps(dict(task), ensure_ascii=False, sort_keys=True)
    return str(task).strip()


def _runtime_text(runtime: object) -> str:
    if isinstance(runtime, str):
        return runtime.strip() or "unspecified"
    for field in ("provider", "name", "kind"):
        value = getattr(runtime, field, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return runtime.__class__.__name__


class ContextEngine:
    """Coordinates sources, retrieval, building, and provenance logging."""

    def __init__(
        self,
        *,
        sources: Sequence[KnowledgeSource],
        project: str,
        project_profile: str,
        project_key: str = "",
        retriever: Retriever | None = None,
        builder: ContextBuilder | None = None,
        logger: ContextLogger | None = None,
        cache: ContextCache | None = None,
        project_profile_version: str = "",
        policy_version: str = "",
        compiler_version: str = "3",
        cache_query_salt: str = "",
    ) -> None:
        self.sources = tuple(sources)
        self.project = project
        self.project_profile = project_profile
        self.project_key = project_key
        self.retriever = retriever or RuleBasedRetriever()
        self.builder = builder or ContextBuilder()
        self.logger = logger
        self.cache = cache
        self.project_profile_version = project_profile_version
        self.policy_version = policy_version
        self.compiler_version = compiler_version
        self.cache_query_salt = cache_query_salt

    @classmethod
    def default(
        cls,
        *,
        control_root: Path,
        project: str,
        project_profile: str,
        project_key: str = "",
        artifacts_dir: Path | None = None,
        context_log_path: Path | None = None,
        obsidian_vaults: Sequence[Path] = (),
        repository: Path | None = None,
        token_budget: int = 12_000,
        runtime_delta: str = "",
        cache_query_salt: str = "",
    ) -> "ContextEngine":
        repository_root = repository.resolve() if repository is not None else control_root.resolve()
        sources: list[KnowledgeSource] = [
            ProjectProfileSource(control_root),
            PolicySource(control_root),
            ContractSource(control_root),
            SkillSource(control_root),
            PrivateProjectKnowledgeSource(control_root),
            RepositoryDocumentationSource(),
            RepositoryMetadataSource(),
        ]
        if artifacts_dir is not None:
            sources.append(ArtifactSource(artifacts_dir))
        if runtime_delta.strip():
            sources.append(RuntimeDeltaSource(runtime_delta))
        sources.append(ObsidianSource.discover(repository_root, obsidian_vaults))
        logger = JsonlContextLogger(context_log_path) if context_log_path is not None else None
        project_profile_version = version_fingerprint(
            (control_root.resolve() / ".agent-project-profiles.yaml",)
        )
        policy_version = version_fingerprint(
            (
                control_root.resolve() / ".agent-policy.yaml",
                control_root.resolve() / ".agent-role-policy.yaml",
                control_root.resolve() / ".agent-tool-policy.yaml",
            )
        )
        return cls(
            sources=sources,
            project=project,
            project_profile=project_profile,
            project_key=project_key,
            retriever=RuleBasedRetriever(),
            builder=ContextBuilder(ContextBudget(total_tokens=token_budget)),
            logger=logger,
            cache=ContextCache(control_root.resolve() / ".agent-cache" / "context"),
            project_profile_version=project_profile_version,
            policy_version=policy_version,
            compiler_version="3",
            cache_query_salt=cache_query_salt,
        )

    def build(self, task: object, repository: Path, role: str, runtime: object) -> Context:
        """Build the only context package that a role should receive."""

        resolved_repository = repository.resolve()
        if self.project_key and self.project_key != trust_key(resolved_repository):
            raise ValueError(
                "context project_key does not match the canonical repository"
            )
        request = KnowledgeRequest(
            task=_task_text(task),
            repository=resolved_repository,
            role=role,
            runtime=_runtime_text(runtime),
            project=self.project,
            project_profile=self.project_profile,
            project_key=self.project_key,
        )
        cache_key: ContextCacheKey | None = None
        if self.cache is not None:
            head_sha, dirty_fingerprint = repository_fingerprints(request.repository)
            cache_key = ContextCacheKey(
                repository_head_sha=head_sha,
                dirty_state_fingerprint=dirty_fingerprint,
                role=request.role,
                query_fingerprint=fingerprint_text(
                    json.dumps(
                        {
                            "task": request.task,
                            "role": request.role,
                            "project": request.project,
                            "project_key": request.project_key,
                            "profile": request.project_profile,
                            "repository_identity": fingerprint_text(
                                str(request.repository)
                            ),
                            "runtime_delta": self.cache_query_salt,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                ),
                project_profile_version=self.project_profile_version,
                policy_version=self.policy_version,
                context_compiler_version=self.compiler_version,
            )
        documents: dict[str, KnowledgeDocument] = {}
        source_counts: dict[str, int] = {}
        for source in self.sources:
            collected = source.collect(request)
            source_counts[source.name] = len(collected)
            for document in collected:
                documents.setdefault(document.id, document)
        deduplicated, duplicates = deduplicate_documents(tuple(documents.values()))
        source_fingerprints = document_fingerprints(deduplicated)
        context_revision = document_revision(
            tuple(documents.values()),
            project_profile_version=self.project_profile_version,
            policy_version=self.policy_version,
            compiler_version=self.compiler_version,
        )
        if self.cache is not None and cache_key is not None:
            exact = self.cache.get(cache_key, context_revision=context_revision)
            if exact is not None:
                cached_context = exact.context(cache_status="hit")
                return attach_log(cached_context, cached_context.log, self.logger)
            self.cache.invalidate_changed_sources(
                source_fingerprints,
                key=cache_key,
                context_revision=context_revision,
            )
            compatible = self.cache.get_compatible(
                cache_key,
                source_fingerprints=source_fingerprints,
                context_revision=context_revision,
            )
            if compatible is not None:
                cached_context = compatible.context(cache_status="compatible_hit")
                self.cache.put(
                    cache_key,
                    cached_context,
                    source_fingerprints=source_fingerprints,
                    context_revision=context_revision,
                )
                return attach_log(cached_context, cached_context.log, self.logger)
        retrieval = self.retriever.retrieve(request, deduplicated)
        context = self.builder.build(request, retrieval)
        event: dict[str, object] = {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task": request.task,
            "repository": str(request.repository),
            "project": request.project,
            "project_key": request.project_key,
            "project_profile": request.project_profile,
            "role": request.role,
            "runtime": request.runtime,
            "retriever": retrieval.algorithm,
            "query_terms": list(retrieval.query_terms),
            "budget": {
                "total_tokens": context.token_budget,
                "used_tokens": context.tokens_used,
                "remaining_tokens": max(0, context.token_budget - context.tokens_used),
            },
            "source_counts": source_counts,
            "deduplication": {
                "candidate_count": len(documents),
                "unique_count": len(deduplicated),
                "removed_count": len(duplicates),
                "removed": [
                    {"id": item.id, "source": item.source, "path": item.path}
                    for item in duplicates
                ],
            },
            "cache": {
                "status": "miss" if cache_key is not None else "disabled",
                "key": cache_key.digest if cache_key is not None else "",
            },
            "context_revision": context_revision,
            "effective_context_digest": fingerprint_text(context.package),
            "selected": [
                {
                    "id": item.document.id,
                    "source": item.document.source,
                    "path": item.document.path,
                    "knowledge_type": item.document.knowledge_type.value,
                    "document_type": item.document.document_type.value,
                    "priority": item.document.priority,
                    "score": round(item.score, 6),
                    "reason": item.reason,
                    "original_tokens": item.original_tokens,
                    "included_tokens": item.included_tokens,
                    "truncated": item.truncated,
                    "privacy": item.document.privacy.value,
                    "trust": item.document.trust.value,
                    "runtime_destination": request.runtime,
                    "reason_code": "included",
                    "metadata": dict(item.document.metadata),
                }
                for item in context.selected
            ],
            "excluded": [
                {
                    "id": item.document.id,
                    "source": item.document.source,
                    "path": item.document.path,
                    "knowledge_type": item.document.knowledge_type.value,
                    "document_type": item.document.document_type.value,
                    "priority": item.document.priority,
                    "score": round(item.score, 6),
                    "reason": item.reason,
                    "reason_code": (
                        "irrelevant"
                        if item.reason == "no_rule_match"
                        else "budget"
                        if "budget" in item.reason
                        else item.reason
                    ),
                    "privacy": item.document.privacy.value,
                    "trust": item.document.trust.value,
                    "runtime_destination": request.runtime,
                    "metadata": dict(item.document.metadata),
                }
                for item in context.excluded
            ]
            + [
                {
                    "id": item.id,
                    "source": item.source,
                    "path": item.path,
                    "knowledge_type": item.knowledge_type.value,
                    "document_type": item.document_type.value,
                    "priority": item.priority,
                    "score": 0.0,
                    "reason": "duplicate",
                    "reason_code": "duplicate",
                    "privacy": item.privacy.value,
                    "trust": item.trust.value,
                    "runtime_destination": request.runtime,
                    "metadata": dict(item.metadata),
                }
                for item in duplicates
            ],
        }
        attached = attach_log(context, event, self.logger)
        if self.cache is not None and cache_key is not None:
            self.cache.put(
                cache_key,
                attached,
                source_fingerprints=source_fingerprints,
                context_revision=context_revision,
            )
        return attached
