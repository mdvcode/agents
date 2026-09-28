"""Typed values shared by the Context Intelligence Platform."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping


class KnowledgeType(StrEnum):
    """The authority/purpose of a context item."""

    DOCUMENTATION = "documentation"
    SKILL = "skill"
    POLICY = "policy"
    CONTRACT = "contract"
    MEMORY = "memory"
    PROJECT_PROFILE = "project_profile"
    REPOSITORY = "repository"
    ARTIFACT = "artifact"


class DocumentType(StrEnum):
    """The concrete representation of a knowledge item."""

    README = "readme"
    ADR = "adr"
    WIKI = "wiki"
    OBSIDIAN = "obsidian"
    PROJECT_DOC = "project_doc"
    AGENTS = "agents"
    PROJECT_PROFILE = "project_profile"
    POLICY = "policy"
    SKILL = "skill"
    CONTRACT = "contract"
    MEMORY = "memory"
    REPOSITORY = "repository"
    ARTIFACT = "artifact"


class PrivacyClass(StrEnum):
    """Where a context item may be sent after policy evaluation."""

    PUBLIC = "public"
    PROJECT_PRIVATE = "project-private"
    LOCAL_ONLY = "local-only"
    SECRET_NEVER_MODEL = "secret-never-model"


class TrustStatus(StrEnum):
    """Whether content may carry authority or is reference-only input."""

    TRUSTED = "trusted"
    UNTRUSTED_REFERENCE = "untrusted-reference"


@dataclass(frozen=True)
class KnowledgeRequest:
    task: str
    repository: Path
    role: str
    runtime: str
    project: str
    project_profile: str
    project_key: str = ""


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    content: str
    source: str
    path: str
    knowledge_type: KnowledgeType
    document_type: DocumentType
    priority: int
    metadata: Mapping[str, str] = field(default_factory=dict)
    privacy: PrivacyClass = PrivacyClass.PROJECT_PRIVATE
    trust: TrustStatus = TrustStatus.UNTRUSTED_REFERENCE


@dataclass(frozen=True)
class RetrievedDocument:
    document: KnowledgeDocument
    score: float
    reason: str


@dataclass(frozen=True)
class RetrievalResult:
    selected: tuple[RetrievedDocument, ...]
    excluded: tuple[RetrievedDocument, ...]
    query_terms: tuple[str, ...]
    algorithm: str


@dataclass(frozen=True)
class ContextSelection:
    document: KnowledgeDocument
    score: float
    reason: str
    original_tokens: int
    included_tokens: int
    truncated: bool


@dataclass(frozen=True)
class ContextExclusion:
    document: KnowledgeDocument
    score: float
    reason: str


@dataclass(frozen=True)
class Context:
    """A complete, bounded role-context package."""

    package: str
    selected: tuple[ContextSelection, ...]
    excluded: tuple[ContextExclusion, ...]
    token_budget: int
    tokens_used: int
    log: Mapping[str, object]
    log_path: str = ""
