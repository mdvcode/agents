"""Swappable retrieval contract and deterministic rule-based implementation."""

from __future__ import annotations

import re
from collections import Counter
from typing import Protocol, Sequence

from .models import (
    DocumentType,
    KnowledgeDocument,
    KnowledgeRequest,
    KnowledgeType,
    RetrievedDocument,
    RetrievalResult,
)


TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
TERM_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "oauth": ("authentication", "authorization", "auth", "oidc", "security", "token"),
    "oidc": ("oauth", "authentication", "authorization", "security", "token"),
    "auth": ("authentication", "authorization", "oauth", "security", "permission"),
    "authentication": ("auth", "oauth", "oidc", "security", "session"),
    "authorization": ("auth", "oauth", "permission", "policy", "security"),
    "database": ("schema", "migration", "orm", "query", "transaction"),
    "api": ("endpoint", "contract", "serializer", "validation"),
    "context": ("knowledge", "retrieval", "rag", "prompt", "budget"),
}


class Retriever(Protocol):
    """Backend boundary for rule, semantic, or hybrid retrieval."""

    name: str

    def retrieve(
        self,
        request: KnowledgeRequest,
        documents: Sequence[KnowledgeDocument],
    ) -> RetrievalResult: ...


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in TOKEN_PATTERN.findall(text) if len(token) > 1)


def expanded_terms(text: str) -> tuple[str, ...]:
    terms = list(tokenize(text))
    expanded = set(terms)
    for term in terms:
        expanded.update(TERM_EXPANSIONS.get(term, ()))
    return tuple(sorted(expanded))


class RuleBasedRetriever:
    """Keyword and authority rules; no embeddings or external services."""

    name = "rule_based_keyword_v1"

    def _rank(self, request: KnowledgeRequest, document: KnowledgeDocument, terms: set[str]) -> RetrievedDocument:
        document_terms = Counter(tokenize(f"{document.path} {document.title} {document.content}"))
        overlap = sum(min(document_terms.get(term, 0), 3) for term in terms)
        score = float(document.priority + overlap * 12)
        reasons: list[str] = []
        always_types = {
            KnowledgeType.PROJECT_PROFILE,
            KnowledgeType.POLICY,
            KnowledgeType.CONTRACT,
            KnowledgeType.ARTIFACT,
        }
        if document.knowledge_type in always_types:
            score += 120
            reasons.append("authoritative_context")
        if document.document_type == DocumentType.README:
            score += 70
            reasons.append("project_overview")
        if document.document_type == DocumentType.AGENTS:
            score += 10
            reasons.append("agent_instructions")
        if document.metadata.get("role_match") == "true":
            score += 90
            reasons.append("role_skill")
        if overlap:
            reasons.append(f"term_overlap:{overlap}")
        if document.document_type == DocumentType.ADR and overlap:
            score += 55
            reasons.append("architecture_match")
        if document.knowledge_type == KnowledgeType.SKILL and {"security", "auth", "oauth"} & terms:
            if {"security", "auth", "permission"} & set(document_terms):
                score += 45
                reasons.append("security_skill_rule")
        selected = bool(reasons)
        return RetrievedDocument(
            document=document,
            score=score if selected else 0.0,
            reason=",".join(reasons) if selected else "no_rule_match",
        )

    def retrieve(
        self,
        request: KnowledgeRequest,
        documents: Sequence[KnowledgeDocument],
    ) -> RetrievalResult:
        query_terms = expanded_terms(f"{request.task} {request.role}")
        terms = set(query_terms)
        ranked = [self._rank(request, document, terms) for document in documents]
        selected = tuple(
            sorted(
                (item for item in ranked if item.score > 0),
                key=lambda item: (-item.score, -item.document.priority, item.document.path, item.document.title),
            )
        )
        excluded = tuple(
            sorted(
                (item for item in ranked if item.score <= 0),
                key=lambda item: (-item.document.priority, item.document.path, item.document.title),
            )
        )
        return RetrievalResult(
            selected=selected,
            excluded=excluded,
            query_terms=query_terms,
            algorithm=self.name,
        )
