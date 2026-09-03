"""Context Intelligence Platform public API."""

from .builder import ContextBudget, ContextBuilder, estimate_tokens
from .cache import CachedContext, ContextCache, ContextCacheKey
from .deduplication import canonicalize, chunk_fingerprint, deduplicate_documents
from .engine import ContextEngine
from .memory import MemoryManager, MemoryRecord
from .models import (
    Context,
    ContextExclusion,
    ContextSelection,
    DocumentType,
    KnowledgeDocument,
    KnowledgeRequest,
    KnowledgeType,
    PrivacyClass,
    RetrievalResult,
    RetrievedDocument,
    TrustStatus,
)
from .retrieval import Retriever, RuleBasedRetriever
from .sources import KnowledgeSource, ObsidianSource

__all__ = [
    "Context",
    "ContextBudget",
    "ContextBuilder",
    "ContextCache",
    "ContextCacheKey",
    "CachedContext",
    "ContextEngine",
    "ContextExclusion",
    "ContextSelection",
    "DocumentType",
    "KnowledgeDocument",
    "KnowledgeRequest",
    "KnowledgeSource",
    "KnowledgeType",
    "PrivacyClass",
    "MemoryManager",
    "MemoryRecord",
    "ObsidianSource",
    "RetrievalResult",
    "RetrievedDocument",
    "Retriever",
    "RuleBasedRetriever",
    "TrustStatus",
    "estimate_tokens",
    "canonicalize",
    "chunk_fingerprint",
    "deduplicate_documents",
]
