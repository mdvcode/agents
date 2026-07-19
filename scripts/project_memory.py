#!/usr/bin/env python3
"""Retrieve scoped project-memory chunks for role context manifests."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_MAX_RESULTS = 6
DEFAULT_MAX_RETRIEVAL_BYTES = 22000
DEFAULT_MAX_SOURCE_BYTES = 256000
DEFAULT_MAX_CHUNK_BYTES = 8000
PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class MemoryChunk:
    path: Path
    display_path: str
    heading: str
    content: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class RankedChunk:
    chunk: MemoryChunk
    score: float


@dataclass(frozen=True)
class MemoryRetrieval:
    query: str
    candidate_paths: tuple[str, ...]
    selected: tuple[RankedChunk, ...]
    context_path: Path | None
    status: str


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in TOKEN_PATTERN.findall(text) if len(token) > 1)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _memory_roots(control_root: Path, project: str, project_profile: str) -> tuple[tuple[Path, ...], str]:
    if project_profile == "agent_workspace":
        return (
            control_root / "docs" / "memory",
            control_root / "docs" / "wiki",
            control_root / "docs" / "graph",
        ), "ready"
    if not PROJECT_NAME_PATTERN.fullmatch(project):
        return (), "invalid_project"
    projects_root = (control_root / "docs" / "projects").resolve()
    project_root = (projects_root / project).resolve()
    if not _is_within(project_root, projects_root):
        return (), "invalid_project"
    if not (project_root / "privacy.md").is_file():
        return (), "privacy_policy_missing"
    return tuple(project_root / name for name in ("memory", "wiki", "graph", "issues")), "ready"


def project_privacy_path(control_root: Path, project: str, project_profile: str) -> Path | None:
    if project_profile == "agent_workspace" or not PROJECT_NAME_PATTERN.fullmatch(project):
        return None
    projects_root = (control_root / "docs" / "projects").resolve()
    privacy = (projects_root / project / "privacy.md").resolve()
    if _is_within(privacy, projects_root) and privacy.is_file() and not privacy.is_symlink():
        return privacy
    return None


def _candidate_files(roots: Sequence[Path], control_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        resolved_root = root.resolve()
        for path in sorted(root.rglob("*.md")):
            if path.is_symlink() or not path.is_file() or not _is_within(path, resolved_root):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if 0 < size <= DEFAULT_MAX_SOURCE_BYTES and _is_within(path, control_root):
                candidates.append(path.resolve())
    return candidates


def _truncate_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip() + "\n[truncated]"


def _section_parts(text: str) -> Iterable[tuple[str, str]]:
    heading = "Document"
    lines: list[str] = []
    for line in text.splitlines():
        match = HEADING_PATTERN.match(line)
        if match and lines:
            yield heading, "\n".join(lines).strip()
            heading = match.group(2)
            lines = [line]
        elif match:
            heading = match.group(2)
            lines.append(line)
        else:
            lines.append(line)
    if lines:
        yield heading, "\n".join(lines).strip()


def _chunks(paths: Sequence[Path], control_root: Path) -> list[MemoryChunk]:
    chunks: list[MemoryChunk] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        display_path = str(path.relative_to(control_root.resolve()))
        for heading, content in _section_parts(text):
            content = _truncate_bytes(content, DEFAULT_MAX_CHUNK_BYTES)
            tokens = tokenize(f"{display_path} {heading} {content}")
            if content and tokens:
                chunks.append(
                    MemoryChunk(
                        path=path,
                        display_path=display_path,
                        heading=heading,
                        content=content,
                        tokens=tokens,
                    )
                )
    return chunks


def rank_chunks(query: str, chunks: Sequence[MemoryChunk]) -> list[RankedChunk]:
    query_terms = Counter(tokenize(query))
    if not query_terms or not chunks:
        return []
    document_frequency = Counter({term: 0 for term in query_terms})
    for chunk in chunks:
        terms = set(chunk.tokens)
        for term in query_terms:
            if term in terms:
                document_frequency[term] += 1
    average_length = sum(len(chunk.tokens) for chunk in chunks) / len(chunks)
    ranked: list[RankedChunk] = []
    for chunk in chunks:
        frequencies = Counter(chunk.tokens)
        score = 0.0
        for term, query_frequency in query_terms.items():
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            inverse_document_frequency = math.log(
                1 + (len(chunks) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(chunk.tokens) / average_length)
            score += query_frequency * inverse_document_frequency * frequency * 2.5 / denominator
        if score > 0:
            ranked.append(RankedChunk(chunk=chunk, score=score))
    return sorted(
        ranked,
        key=lambda item: (-item.score, item.chunk.display_path, item.chunk.heading),
    )


def _select_with_budget(
    ranked: Sequence[RankedChunk],
    *,
    max_results: int,
    max_bytes: int,
) -> tuple[RankedChunk, ...]:
    selected: list[RankedChunk] = []
    used_bytes = 0
    for item in ranked:
        if len(selected) >= max_results:
            break
        content_bytes = len(item.chunk.content.encode("utf-8"))
        if content_bytes > max_bytes - used_bytes:
            continue
        selected.append(item)
        used_bytes += content_bytes
    return tuple(selected)


def _write_context(path: Path, query: str, selected: Sequence[RankedChunk]) -> None:
    lines = [
        "# Retrieved project memory",
        "",
        "Private control-plane context. Do not copy it into public outputs without sanitization.",
        "",
        f"Query: {query}",
    ]
    for rank, item in enumerate(selected, start=1):
        lines.extend(
            [
                "",
                f"## {rank}. {item.chunk.display_path} — {item.chunk.heading}",
                f"Score: {item.score:.6f}",
                "",
                item.chunk.content,
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def retrieve_project_memory(
    *,
    control_root: Path,
    project: str,
    project_profile: str,
    query: str,
    context_path: Path,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_bytes: int = DEFAULT_MAX_RETRIEVAL_BYTES,
) -> MemoryRetrieval:
    roots, status = _memory_roots(control_root.resolve(), project, project_profile)
    if status != "ready":
        return MemoryRetrieval(query, (), (), None, status)
    candidates = _candidate_files(roots, control_root.resolve())
    display_candidates = tuple(str(path.relative_to(control_root.resolve())) for path in candidates)
    selected = _select_with_budget(
        rank_chunks(query, _chunks(candidates, control_root.resolve())),
        max_results=max_results,
        max_bytes=max_bytes,
    )
    if not selected:
        return MemoryRetrieval(query, display_candidates, (), None, "no_matches")
    _write_context(context_path, query, selected)
    return MemoryRetrieval(query, display_candidates, selected, context_path.resolve(), "retrieved")
