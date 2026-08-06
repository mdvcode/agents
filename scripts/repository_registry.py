#!/usr/bin/env python3
"""Trusted repository registry helpers."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.project import (
    ProjectConfigError,
    load_project_config,
    project_is_trusted,
    safe_branch_prefix,
)


REGISTRY = ROOT / ".agent-repositories.yaml"
FORBIDDEN_PUBLIC_BRANCH_TOKENS = {"agent", "agents", "ai", "automation", "codex"}


def safe_public_branch_prefix(value: str) -> bool:
    """Accept configurable Git-safe prefixes without exposing internal tooling names."""

    tokens = {token for token in re.split(r"[._/-]+", value.lower()) if token}
    return safe_branch_prefix(value) and not tokens.intersection(FORBIDDEN_PUBLIC_BRANCH_TOKENS)


def validate_branch_prefixes(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{label} must be a non-empty list"]
    if len(value) != len(set(item for item in value if isinstance(item, str))):
        return [f"{label} must not contain duplicates"]
    invalid = [item for item in value if not isinstance(item, str) or not safe_public_branch_prefix(item)]
    return [f"{label} contains an unsafe prefix: {item!r}" for item in invalid]


@dataclass(frozen=True)
class RepositoryRecord:
    repository_id: str
    project_profile: str
    expected_remotes: tuple[str, ...]
    base_branch: str
    allowed_branch_prefixes: tuple[str, ...]
    protected_paths: tuple[str, ...]
    source: str = "central_registry"


def load_registry(path: Path = REGISTRY) -> dict[str, RepositoryRecord]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(".agent-repositories.yaml must contain version: 1")
    repositories = data.get("repositories")
    if not isinstance(repositories, dict):
        raise ValueError(".agent-repositories.yaml must contain repositories")
    records: dict[str, RepositoryRecord] = {}
    for repository_id, raw in repositories.items():
        if not isinstance(repository_id, str) or not isinstance(raw, dict):
            continue
        records[repository_id] = RepositoryRecord(
            repository_id=repository_id,
            project_profile=str(raw.get("project_profile", "")),
            expected_remotes=tuple(item for item in raw.get("expected_remotes", []) if isinstance(item, str)),
            base_branch=str(raw.get("base_branch", "")),
            allowed_branch_prefixes=tuple(
                item for item in raw.get("allowed_branch_prefixes", []) if isinstance(item, str)
            ),
            protected_paths=tuple(item for item in raw.get("protected_paths", []) if isinstance(item, str)),
            source="central_registry",
        )
    return records


def find_by_remote(remote_url: str, path: Path = REGISTRY) -> RepositoryRecord | None:
    for record in load_registry(path).values():
        if remote_url in record.expected_remotes:
            return record
    return None


def load_local_project_record(repository: Path) -> RepositoryRecord | None:
    """Load local execution identity without granting publication authority."""
    path = repository.resolve() / ".agent" / "project.yaml"
    if not path.is_file():
        return None
    try:
        config = load_project_config(repository)
        trusted = project_is_trusted(config)
    except ProjectConfigError as exc:
        raise ValueError(str(exc)) from exc
    if not trusted:
        return None
    return RepositoryRecord(
        repository_id=config.project_id,
        project_profile=config.profile,
        expected_remotes=(),
        base_branch=config.base_branch,
        allowed_branch_prefixes=(config.branch_prefix,),
        protected_paths=(),
        source="local_project_config",
    )


def validate_registry_data(data: Any, label: str = ".agent-repositories.yaml") -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{label}: top-level value must be an object"]
    if data.get("version") != 1:
        errors.append(f"{label}: version must be 1")
    repositories = data.get("repositories")
    if not isinstance(repositories, dict):
        return errors + [f"{label}: repositories must be an object"]
    for repository_id, raw in repositories.items():
        if not isinstance(raw, dict):
            errors.append(f"{label}: repositories.{repository_id} must be an object")
            continue
        if raw.get("project_profile") not in {"agent_workspace", "django", "nextjs_web"}:
            errors.append(f"{label}: repositories.{repository_id}.project_profile is invalid")
        expected_remotes = raw.get("expected_remotes")
        if not isinstance(expected_remotes, list) or not expected_remotes:
            errors.append(f"{label}: repositories.{repository_id}.expected_remotes must be a non-empty list")
        if not isinstance(raw.get("base_branch"), str) or not raw.get("base_branch"):
            errors.append(f"{label}: repositories.{repository_id}.base_branch must be a non-empty string")
        errors.extend(
            validate_branch_prefixes(
                raw.get("allowed_branch_prefixes"),
                f"{label}: repositories.{repository_id}.allowed_branch_prefixes",
            )
        )
        protected_paths = raw.get("protected_paths")
        if not isinstance(protected_paths, list) or not protected_paths:
            errors.append(f"{label}: repositories.{repository_id}.protected_paths must be a non-empty list")
    return errors
