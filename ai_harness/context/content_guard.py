"""Deterministic context privacy checks; never retain detected values in findings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Iterator

import yaml

from .models import KnowledgeDocument, PrivacyClass


GUARD_VERSION = "1"
REDACTED = "[REDACTED]"
# High-confidence credentials and literal credential assignments. This is a
# bounded heuristic, not a claim to recognize every possible sensitive value.
PATTERNS = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
        ),
    ),
    (
        "provider_key",
        re.compile(
            r"\b(?:sk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b"
        ),
    ),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    ("credential_url", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.I)),
    (
        "credential_assignment",
        re.compile(
            r"""(?im)["']?\b(?:[a-z0-9]+_)*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|client[_-]?secret|secret[_-]?key)["']?\s*[:=]\s*["']?([^\s"',;}{]{8,})"""
        ),
    ),
)


class ContextGuardError(ValueError):
    """A context input cannot safely cross the configured runtime boundary."""


def _matches(text: str) -> Iterator[tuple[str, re.Match[str]]]:
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            if kind == "credential_assignment":
                value = match.group(1)
                if (
                    value.startswith(("${", "$", "<", "os.environ", "process.env"))
                    or value == REDACTED
                ):
                    continue
            yield kind, match


def findings(text: str) -> tuple[str, ...]:
    return tuple(sorted({kind for kind, _ in _matches(text)}))


def redact_text(text: str) -> str:
    spans = sorted((match.start(), match.end()) for _, match in _matches(text))
    parts: list[str] = []
    end = 0
    for start, stop in spans:
        if stop <= end:
            continue
        parts.extend((text[end : max(end, start)], REDACTED))
        end = stop
    parts.append(text[end:])
    return "".join(parts)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            redact_text(str(key)): (
                REDACTED
                if re.fullmatch(
                    r"(?i)(?:api[_-]?key|password|passwd|client[_-]?secret|access[_-]?token|auth[_-]?token)",
                    str(key),
                )
                and isinstance(item, str)
                and item
                else redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


def require_safe(text: str, label: str = "context") -> None:
    kinds = findings(text)
    if kinds:
        raise ContextGuardError(
            f"{label} contains credential-like content ({', '.join(kinds)}); remove it before sending"
        )


def require_safe_value(value: Any, label: str = "context") -> None:
    if isinstance(value, str):
        require_safe(value, label)
    elif isinstance(value, dict):
        for key, item in value.items():
            require_safe(str(key), label)
            if isinstance(item, str):
                require_safe(f"{key}={item}", label)
            require_safe_value(item, label)
    elif isinstance(value, (tuple, list)):
        for item in value:
            require_safe_value(item, label)


@dataclass(frozen=True)
class ContextPrivacyPolicy:
    # These are the already-supported subscription adapters. Unknown/new
    # destinations fail closed; local-only is never sent to a model in this phase.
    private_destinations: tuple[str, ...] = ("codex-sdk", "codex-cli")

    @classmethod
    def load(cls, control_root: Path, project: str) -> "ContextPrivacyPolicy":
        path = control_root / ".agent-policy.yaml"
        if not path.exists():
            return cls()
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ContextGuardError(
                "Cannot load central context privacy policy"
            ) from exc
        if not isinstance(document, dict):
            raise ContextGuardError("Invalid central context privacy policy")
        config = document.get("context_privacy", {})
        if not isinstance(config, dict):
            raise ContextGuardError("Invalid context_privacy policy")
        destinations = config.get(
            "project_private_destinations", list(cls().private_destinations)
        )
        projects = config.get("projects", {})
        if not isinstance(projects, dict):
            raise ContextGuardError("Invalid context privacy projects")
        override = projects.get(project, {})
        if not isinstance(override, dict):
            raise ContextGuardError("Invalid project context privacy policy")
        destinations = override.get("project_private_destinations", destinations)
        if not isinstance(destinations, list) or any(
            not isinstance(x, str) for x in destinations
        ):
            raise ContextGuardError("Context destinations must be a list")
        return cls(tuple(sorted(set(destinations))))

    def exclusion_reason(self, document: KnowledgeDocument, runtime: str) -> str:
        if document.privacy in {
            PrivacyClass.LOCAL_ONLY,
            PrivacyClass.SECRET_NEVER_MODEL,
        }:
            return "privacy"
        if (
            document.privacy == PrivacyClass.PROJECT_PRIVATE
            and runtime not in self.private_destinations
        ):
            return "privacy"
        if findings(document.content):
            return "secret"
        return ""


def source_privacy(content: str) -> PrivacyClass:
    """Read a restrictive Markdown frontmatter label, never instruction authority."""
    if not content.startswith("---\n"):
        return PrivacyClass.PROJECT_PRIVATE
    frontmatter, separator, _ = content[4:].partition("\n---")
    if not separator:
        return PrivacyClass.LOCAL_ONLY
    try:
        metadata = yaml.safe_load(frontmatter)
        value = (
            metadata.get("privacy", "project-private")
            if isinstance(metadata, dict)
            else "project-private"
        )
        return PrivacyClass(value)
    except (yaml.YAMLError, TypeError, ValueError):
        # Malformed classification is withheld, not silently downgraded.
        return PrivacyClass.LOCAL_ONLY
