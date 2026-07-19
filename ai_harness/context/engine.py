"""Single entry point for knowledge discovery and role-context compilation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .builder import ContextBudget, ContextBuilder
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
        retriever: Retriever | None = None,
        builder: ContextBuilder | None = None,
        logger: ContextLogger | None = None,
    ) -> None:
        self.sources = tuple(sources)
        self.project = project
        self.project_profile = project_profile
        self.retriever = retriever or RuleBasedRetriever()
        self.builder = builder or ContextBuilder()
        self.logger = logger

    @classmethod
    def default(
        cls,
        *,
        control_root: Path,
        project: str,
        project_profile: str,
        artifacts_dir: Path | None = None,
        context_log_path: Path | None = None,
        obsidian_vaults: Sequence[Path] = (),
        repository: Path | None = None,
        token_budget: int = 12_000,
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
        sources.append(ObsidianSource.discover(repository_root, obsidian_vaults))
        logger = JsonlContextLogger(context_log_path) if context_log_path is not None else None
        return cls(
            sources=sources,
            project=project,
            project_profile=project_profile,
            retriever=RuleBasedRetriever(),
            builder=ContextBuilder(ContextBudget(total_tokens=token_budget)),
            logger=logger,
        )

    def build(self, task: object, repository: Path, role: str, runtime: object) -> Context:
        """Build the only context package that a role should receive."""

        request = KnowledgeRequest(
            task=_task_text(task),
            repository=repository.resolve(),
            role=role,
            runtime=_runtime_text(runtime),
            project=self.project,
            project_profile=self.project_profile,
        )
        documents: dict[str, KnowledgeDocument] = {}
        source_counts: dict[str, int] = {}
        for source in self.sources:
            collected = source.collect(request)
            source_counts[source.name] = len(collected)
            for document in collected:
                documents.setdefault(document.id, document)
        retrieval = self.retriever.retrieve(request, tuple(documents.values()))
        context = self.builder.build(request, retrieval)
        event: dict[str, object] = {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task": request.task,
            "repository": str(request.repository),
            "project": request.project,
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
                }
                for item in context.excluded
            ],
        }
        return attach_log(context, event, self.logger)
