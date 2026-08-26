"""Canonical exact/near duplicate removal before role model calls."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime

from .models import KnowledgeDocument, KnowledgeType


def canonicalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def chunk_fingerprint(text: str) -> str:
    return hashlib.sha256(canonicalize(text).encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", canonicalize(text), re.UNICODE))


def _near_duplicate(left: str, right: str, threshold: float) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= threshold


def _created_timestamp(document: KnowledgeDocument) -> float:
    value = str(document.metadata.get("created_at", ""))
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _superseded_refs(document: KnowledgeDocument) -> set[str]:
    raw = str(document.metadata.get("supersedes", ""))
    return {item.strip() for item in re.split(r"[,;\n]", raw) if item.strip()}


def deduplicate_documents(
    documents: Sequence[KnowledgeDocument],
    *,
    near_duplicate_threshold: float = 0.94,
) -> tuple[tuple[KnowledgeDocument, ...], tuple[KnowledgeDocument, ...]]:
    """Prefer newer authoritative/high-priority sources and remove redundant copies."""

    ordered = sorted(
        documents,
        key=lambda document: (
            str(document.metadata.get("authoritative", "")).casefold() != "true",
            document.knowledge_type not in {KnowledgeType.POLICY, KnowledgeType.CONTRACT, KnowledgeType.ARTIFACT},
            -_created_timestamp(document),
            -document.priority,
            document.path,
        ),
    )
    kept: list[KnowledgeDocument] = []
    removed: list[KnowledgeDocument] = []
    fingerprints: set[str] = set()
    superseded = {
        reference
        for document in ordered
        for reference in _superseded_refs(document)
    }
    for document in ordered:
        if document.id in superseded or document.path in superseded:
            removed.append(document)
            continue
        fingerprint = chunk_fingerprint(document.content)
        if fingerprint in fingerprints or any(
            _near_duplicate(document.content, existing.content, near_duplicate_threshold)
            for existing in kept
        ):
            removed.append(document)
            continue
        fingerprints.add(fingerprint)
        kept.append(document)
    return tuple(kept), tuple(removed)
