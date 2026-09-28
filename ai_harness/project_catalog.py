"""Safe read model for locally registered Harness projects.

The local trust registry is the only catalog source.  This module deliberately
does not discover repositories, enumerate directories, or expose repository
contents.  A project key is the existing SHA-256 trust key for the canonical
repository path; the configured project id remains display metadata and is not
assumed to be unique.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .project import (
    CONFIG_RELATIVE_PATH,
    ProjectConfigError,
    config_fingerprint,
    load_local_trust,
    load_project_config,
    local_trust_path,
    trust_key,
)


PROJECT_STATES = frozenset({"ready", "missing", "invalid_config", "needs_reinit"})
PROJECT_KEY_PATTERN = re.compile(r"[0-9a-f]{64}")
ACTIVE_TASK_STATUSES = frozenset(
    {"claimed", "leased", "running", "repairing", "resuming", "waiting_children"}
)
ATTENTION_TASK_STATUSES = frozenset(
    {"awaiting_approval", "blocked", "dead_letter", "failed"}
)
CODEX_CONFLICT_TASK_STATUSES = frozenset(
    {
        "queued",
        "claimed",
        "leased",
        "running",
        "repairing",
        "resuming",
        "retry_wait",
        "waiting_children",
        "awaiting_approval",
        "blocked",
    }
)


class ProjectCatalogError(ValueError):
    """Raised when the local project registry cannot be read safely."""


class ProjectNotFoundError(ProjectCatalogError):
    """Raised when an opaque project key is not registered."""


class ProjectUnavailableError(ProjectCatalogError):
    """Raised when a registered project is not currently safe to use."""

    def __init__(self, project_key: str, state: str) -> None:
        super().__init__(f"project is not ready ({state})")
        self.project_key = project_key
        self.state = state


def _safe_string(value: Any, *, limit: int = 256) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _registered_repository(entry: Any) -> tuple[Path | None, str]:
    if not isinstance(entry, Mapping):
        return None, ""
    raw_repository = _safe_string(entry.get("repository"), limit=4096)
    if not raw_repository:
        return None, ""
    candidate = Path(raw_repository).expanduser()
    if not candidate.is_absolute():
        return None, raw_repository
    return candidate.resolve(), raw_repository


def _registry_document(path: Path | None) -> dict[str, Any]:
    try:
        document = load_local_trust(path or local_trust_path())
    except ProjectConfigError as exc:
        raise ProjectCatalogError(str(exc)) from exc
    projects = document.get("projects")
    if not isinstance(projects, dict):
        raise ProjectCatalogError("local project trust must contain projects object")
    return document


def _status_message(state: str) -> str:
    return {
        "ready": "Project is ready.",
        "missing": "Project folder is missing.",
        "invalid_config": "Project registration or configuration is invalid.",
        "needs_reinit": "Run agent init again to refresh project trust.",
    }[state]


def _base_entry(project_key: str, registered: Any) -> dict[str, Any]:
    repository, raw_repository = _registered_repository(registered)
    mapping = registered if isinstance(registered, Mapping) else {}
    project_id = _safe_string(mapping.get("project_id"))
    return {
        "project_key": project_key,
        "project_id": project_id,
        "display_name": project_id or (repository.name if repository else "Project"),
        "repository": str(repository) if repository is not None else raw_repository,
        "profile": _safe_string(mapping.get("profile")),
        "runtime_provider": "",
        "base_branch": "",
        "branch_prefix": "",
        "state": "invalid_config",
        "status_message": _status_message("invalid_config"),
        "can_create_tasks": False,
    }


def _catalog_entry(project_key: str, registered: Any) -> dict[str, Any]:
    entry = _base_entry(project_key, registered)
    repository, raw_repository = _registered_repository(registered)
    if (
        PROJECT_KEY_PATTERN.fullmatch(project_key) is None
        or repository is None
        or raw_repository != str(repository)
        or trust_key(repository) != project_key
        or not isinstance(registered, Mapping)
    ):
        return entry

    if not repository.is_dir():
        state = "missing"
    elif not (repository / CONFIG_RELATIVE_PATH).is_file():
        state = "needs_reinit"
    else:
        try:
            config = load_project_config(repository)
        except ProjectConfigError:
            state = "invalid_config"
        else:
            entry.update(
                {
                    "project_id": config.project_id,
                    "display_name": config.project_id,
                    "profile": config.profile,
                    "runtime_provider": config.runtime_provider,
                    "base_branch": config.base_branch,
                    "branch_prefix": config.branch_prefix,
                }
            )
            trusted = (
                registered.get("repository") == str(repository)
                and registered.get("project_id") == config.project_id
                and registered.get("profile") == config.profile
                and registered.get("config_fingerprint")
                == config_fingerprint(config)
            )
            state = "ready" if trusted else "needs_reinit"

    entry.update(
        {
            "state": state,
            "status_message": _status_message(state),
            "can_create_tasks": state == "ready",
        }
    )
    return entry


def _record_repository(record: Mapping[str, Any]) -> str:
    payload = record.get("payload")
    raw_repository = (
        payload.get("repository")
        if isinstance(payload, Mapping)
        else record.get("repository")
    )
    if not isinstance(raw_repository, str) or not raw_repository.strip():
        raw_repository = record.get("repository")
    if not isinstance(raw_repository, str) or not raw_repository.strip():
        return ""
    return str(Path(raw_repository).expanduser().resolve())


def _aggregate_records(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    for record in records:
        repository = _record_repository(record)
        if not repository:
            continue
        aggregate = aggregates.setdefault(
            repository,
            {"statuses": Counter(), "total": 0, "last_activity_at": 0.0},
        )
        status = _safe_string(record.get("status"), limit=64) or "unknown"
        aggregate["statuses"][status] += 1
        aggregate["total"] += 1
        updated_at = record.get("updated_at", 0)
        if isinstance(updated_at, (int, float)) and not isinstance(updated_at, bool):
            aggregate["last_activity_at"] = max(
                float(aggregate["last_activity_at"]), float(updated_at)
            )
    return aggregates


def load_project_catalog(
    *,
    registry_path: Path | None = None,
    tasks: Iterable[Mapping[str, Any]] = (),
    runs: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Return registered projects plus bounded aggregate execution metadata."""

    document = _registry_document(registry_path)
    task_aggregates = _aggregate_records(tasks)
    run_aggregates = _aggregate_records(runs)
    projects: list[dict[str, Any]] = []
    for raw_key, registered in document["projects"].items():
        project_key = raw_key if isinstance(raw_key, str) else str(raw_key)
        entry = _catalog_entry(project_key, registered)
        repository = entry["repository"]
        task_data = task_aggregates.get(repository, {})
        run_data = run_aggregates.get(repository, {})
        task_statuses = Counter(task_data.get("statuses", {}))
        run_statuses = Counter(run_data.get("statuses", {}))
        entry.update(
            {
                "counts": {
                    "tasks": int(task_data.get("total", 0)),
                    "runs": int(run_data.get("total", 0)),
                    "active_tasks": sum(
                        task_statuses[status] for status in ACTIVE_TASK_STATUSES
                    ),
                    "attention_tasks": sum(
                        task_statuses[status] for status in ATTENTION_TASK_STATUSES
                    ),
                    "codex_conflicts": sum(
                        task_statuses[status]
                        for status in CODEX_CONFLICT_TASK_STATUSES
                    ),
                },
                "task_status_counts": dict(sorted(task_statuses.items())),
                "run_status_counts": dict(sorted(run_statuses.items())),
                "last_activity_at": max(
                    float(task_data.get("last_activity_at", 0.0)),
                    float(run_data.get("last_activity_at", 0.0)),
                ),
            }
        )
        projects.append(entry)
    return sorted(
        projects,
        key=lambda item: (
            item["state"] != "ready",
            -float(item["last_activity_at"]),
            str(item["project_id"]).casefold(),
            str(item["project_key"]),
        ),
    )


def get_project(
    project_key: str,
    *,
    registry_path: Path | None = None,
    tasks: Iterable[Mapping[str, Any]] = (),
    runs: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return one registered catalog entry without treating ids as paths."""

    if PROJECT_KEY_PATTERN.fullmatch(project_key) is None:
        raise ProjectNotFoundError("project not found")
    for project in load_project_catalog(
        registry_path=registry_path, tasks=tasks, runs=runs
    ):
        if project["project_key"] == project_key:
            return project
    raise ProjectNotFoundError("project not found")


def resolve_project_key(
    project_key: str, *, registry_path: Path | None = None
) -> Path:
    """Resolve a ready opaque project key to its trusted canonical repository."""

    project = get_project(project_key, registry_path=registry_path)
    state = str(project["state"])
    if state != "ready":
        raise ProjectUnavailableError(project_key, state)
    return Path(str(project["repository"]))
