"""Content-addressed cache for compiled role context packages."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import Context, KnowledgeDocument


CACHE_VERSION = 1
IGNORED_PARTS = {".git", ".agent-cache", ".agent-runs", ".agent-queue", "__pycache__", "node_modules"}


def fingerprint_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint_file(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def repository_fingerprints(repository: Path) -> tuple[str, str]:
    """Return HEAD and a content-sensitive dirty-state fingerprint."""

    repository = repository.resolve()
    head = _git(repository, "rev-parse", "HEAD") or "no-head"
    status_result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    status = status_result.stdout if status_result.returncode == 0 else ""
    if head != "no-head":
        parts = [status]
        records = status.split("\0")
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if len(record) < 4:
                continue
            relative = record[3:]
            candidate = repository / relative
            parts.append(f"{relative}\0{fingerprint_file(candidate)}")
            if "R" in record[:2] or "C" in record[:2]:
                if index < len(records) and records[index]:
                    previous = records[index]
                    parts.append(f"{previous}\0{fingerprint_file(repository / previous)}")
                index += 1
        return head, fingerprint_text("\n".join(parts))
    entries: list[str] = []
    for path in sorted(repository.rglob("*")):
        if len(entries) >= 400:
            break
        if not path.is_file() or path.is_symlink() or set(path.relative_to(repository).parts) & IGNORED_PARTS:
            continue
        entries.append(f"{path.relative_to(repository)}\0{fingerprint_file(path)}")
    return head, fingerprint_text("\n".join(entries))


def version_fingerprint(paths: tuple[Path, ...]) -> str:
    values = [f"{path.name}\0{fingerprint_file(path)}" for path in paths]
    return fingerprint_text("\n".join(values))


def document_fingerprints(documents: tuple[KnowledgeDocument, ...]) -> dict[str, str]:
    return {
        f"{document.source}:{document.path}": fingerprint_text(document.content)
        for document in documents
    }


@dataclass(frozen=True)
class ContextCacheKey:
    repository_head_sha: str
    dirty_state_fingerprint: str
    role: str
    query_fingerprint: str
    project_profile_version: str
    policy_version: str
    context_compiler_version: str

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def compatible_identity(self) -> tuple[str, ...]:
        return (
            self.repository_head_sha,
            self.role,
            self.query_fingerprint,
            self.project_profile_version,
            self.policy_version,
            self.context_compiler_version,
        )


@dataclass(frozen=True)
class CachedContext:
    key: ContextCacheKey
    retrieved_sources: tuple[Mapping[str, str], ...]
    compiled_context: str
    token_estimate: int
    token_budget: int
    source_fingerprints: Mapping[str, str]
    context_log: Mapping[str, object]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "version": CACHE_VERSION,
            "key": asdict(self.key),
            "retrieved_sources": [dict(item) for item in self.retrieved_sources],
            "compiled_context": self.compiled_context,
            "token_estimate": self.token_estimate,
            "token_budget": self.token_budget,
            "source_fingerprints": dict(self.source_fingerprints),
            "context_log": dict(self.context_log),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CachedContext":
        raw_key = value.get("key")
        if value.get("version") != CACHE_VERSION or not isinstance(raw_key, dict):
            raise ValueError("unsupported context cache entry")
        return cls(
            key=ContextCacheKey(**{field: str(raw_key[field]) for field in ContextCacheKey.__dataclass_fields__}),
            retrieved_sources=tuple(
                dict(item)
                for item in value.get("retrieved_sources", [])
                if isinstance(item, dict)
            ),
            compiled_context=str(value.get("compiled_context", "")),
            token_estimate=int(value.get("token_estimate", 0)),
            token_budget=int(value.get("token_budget", 0)),
            source_fingerprints=dict(value.get("source_fingerprints", {})),
            context_log=dict(value.get("context_log", {})),
            created_at=str(value.get("created_at", "")),
        )

    def context(self, *, cache_status: str) -> Context:
        event = dict(self.context_log)
        event["cache"] = {
            "status": cache_status,
            "key": self.key.digest,
            "created_at": self.created_at,
        }
        return Context(
            package=self.compiled_context,
            selected=(),
            excluded=(),
            token_budget=self.token_budget,
            tokens_used=self.token_estimate,
            log=event,
        )


class ContextCache:
    """Persistent local cache with exact hits and source-scoped compatible reuse."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def _path(self, key: ContextCacheKey) -> Path:
        return self.root / f"{key.digest}.json"

    @staticmethod
    def _read(path: Path) -> CachedContext | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return CachedContext.from_dict(value) if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def get(self, key: ContextCacheKey) -> CachedContext | None:
        return self._read(self._path(key))

    def get_compatible(
        self,
        key: ContextCacheKey,
        *,
        source_fingerprints: Mapping[str, str],
    ) -> CachedContext | None:
        if not self.root.is_dir():
            return None
        for path in sorted(self.root.glob("*.json"), reverse=True)[:1000]:
            entry = self._read(path)
            if entry is None or entry.key.compatible_identity() != key.compatible_identity():
                continue
            if all(
                source_fingerprints.get(source_id) == fingerprint
                for source_id, fingerprint in entry.source_fingerprints.items()
            ):
                return entry
        return None

    def put(
        self,
        key: ContextCacheKey,
        context: Context,
        *,
        source_fingerprints: Mapping[str, str],
    ) -> CachedContext:
        self._prepare()
        retrieved_sources = tuple(
            {
                "id": str(item.get("id", "")),
                "source": str(item.get("source", "")),
                "path": str(item.get("path", "")),
            }
            for item in context.log.get("selected", [])
            if isinstance(item, dict)
        )
        selected_ids = {
            f"{item['source']}:{item['path']}"
            for item in retrieved_sources
            if item.get("source") and item.get("path")
        }
        selected_fingerprints = {
            source_id: fingerprint
            for source_id, fingerprint in source_fingerprints.items()
            if source_id in selected_ids
        }
        entry = CachedContext(
            key=key,
            retrieved_sources=retrieved_sources,
            compiled_context=context.package,
            token_estimate=context.tokens_used,
            token_budget=context.token_budget,
            source_fingerprints=selected_fingerprints,
            context_log=dict(context.log),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        target = self._path(key)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.root,
            prefix=".context-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(entry.as_dict(), handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.chmod(0o600)
        os.replace(temporary, target)
        return entry

    def invalidate_paths(self, paths: set[str]) -> tuple[str, ...]:
        """Remove only entries that retrieved one of the changed source paths."""

        normalized = {value.replace("\\", "/").lstrip("./") for value in paths}
        removed: list[str] = []
        if not self.root.is_dir():
            return ()
        for path in self.root.glob("*.json"):
            entry = self._read(path)
            if entry is None:
                continue
            sources = {
                str(item.get("path", "")).replace("\\", "/").lstrip("./")
                for item in entry.retrieved_sources
            }
            if sources & normalized:
                path.unlink(missing_ok=True)
                removed.append(path.stem)
        return tuple(sorted(removed))

    def invalidate_changed_sources(
        self,
        current: Mapping[str, str],
        *,
        key: ContextCacheKey | None = None,
    ) -> tuple[str, ...]:
        """Evict only entries whose previously selected inputs are stale or missing."""

        removed: list[str] = []
        if not self.root.is_dir():
            return ()
        for path in self.root.glob("*.json"):
            entry = self._read(path)
            if entry is None:
                continue
            if key is not None and entry.key.compatible_identity() != key.compatible_identity():
                continue
            if any(current.get(source_id) != value for source_id, value in entry.source_fingerprints.items()):
                path.unlink(missing_ok=True)
                removed.append(path.stem)
        return tuple(sorted(removed))
