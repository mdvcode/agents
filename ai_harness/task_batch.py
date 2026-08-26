"""Validated batch task manifests shared by the CLI and loopback API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .project import ProjectConfigError, load_local_trust


MAX_BATCH_TASKS = 50
MAX_REPOSITORY_PARALLEL_TASKS = 32
TASK_FIELDS = {
    "repo",
    "goal",
    "parallel",
    "task_id",
    "mode",
    "priority",
    "max_retries",
    "max_parallel_tasks",
}


class BatchManifestError(ValueError):
    """Raised when a batch manifest cannot be normalized safely."""


def _positive_limit(value: Any, label: str, default: int = 0) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise BatchManifestError(f"{label} must be an integer")
    try:
        selected = int(value)
    except (TypeError, ValueError) as exc:
        raise BatchManifestError(f"{label} must be an integer") from exc
    if selected < 1 or selected > MAX_REPOSITORY_PARALLEL_TASKS:
        raise BatchManifestError(
            f"{label} must be between 1 and {MAX_REPOSITORY_PARALLEL_TASKS}"
        )
    return selected


def _registered_projects() -> dict[str, Path]:
    try:
        document = load_local_trust()
    except ProjectConfigError:
        return {}
    projects = document.get("projects", {})
    if not isinstance(projects, dict):
        return {}
    resolved: dict[str, Path] = {}
    for raw in projects.values():
        if not isinstance(raw, dict):
            continue
        project_id = str(raw.get("project_id", "")).strip()
        repository = Path(str(raw.get("repository", ""))).expanduser().resolve()
        if project_id and repository.is_dir():
            resolved[project_id] = repository
    return resolved


def _repository_definitions(
    value: Any,
    *,
    base_dir: Path,
) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BatchManifestError("repositories must be an object")
    registered = _registered_projects()
    definitions: dict[str, dict[str, Any]] = {}
    for alias, raw in value.items():
        if not isinstance(alias, str) or not alias.strip():
            raise BatchManifestError("repository aliases must be non-empty strings")
        if isinstance(raw, str):
            raw = {"path": raw}
        if not isinstance(raw, dict):
            raise BatchManifestError(f"repositories.{alias} must be an object or path")
        unknown = sorted(set(raw) - {"path", "max_parallel_tasks"})
        if unknown:
            raise BatchManifestError(
                f"repositories.{alias} has unexpected fields: {', '.join(unknown)}"
            )
        raw_path = str(raw.get("path", "")).strip()
        if raw_path:
            candidate = Path(raw_path).expanduser()
            repository = (candidate if candidate.is_absolute() else base_dir / candidate).resolve()
        else:
            repository = registered.get(alias, (base_dir / alias).resolve())
        if not repository.is_dir():
            raise BatchManifestError(
                f"repository {alias!r} does not resolve to an existing project folder"
            )
        definitions[alias] = {
            "repository": repository,
            "max_parallel_tasks": _positive_limit(
                raw.get("max_parallel_tasks"),
                f"repositories.{alias}.max_parallel_tasks",
                0,
            ),
        }
    return definitions


def parse_batch_manifest(
    value: str | bytes | dict[str, Any],
    *,
    base_dir: Path,
) -> list[dict[str, Any]]:
    """Return a bounded list of task arguments from YAML or an object."""

    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            document = yaml.safe_load(value)
        except yaml.YAMLError as exc:
            raise BatchManifestError(f"batch YAML is invalid: {exc}") from exc
    else:
        document = value
    if not isinstance(document, dict):
        raise BatchManifestError("batch manifest must be an object")
    unknown_top = sorted(set(document) - {"version", "repositories", "tasks"})
    if unknown_top:
        raise BatchManifestError(
            "batch manifest has unexpected fields: " + ", ".join(unknown_top)
        )
    if document.get("version", 1) != 1:
        raise BatchManifestError("batch manifest version must be 1")
    repositories = _repository_definitions(
        document.get("repositories"), base_dir=base_dir.resolve()
    )
    tasks = document.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise BatchManifestError("tasks must be a non-empty list")
    if len(tasks) > MAX_BATCH_TASKS:
        raise BatchManifestError(f"a batch may contain at most {MAX_BATCH_TASKS} tasks")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(tasks):
        label = f"tasks[{index}]"
        if not isinstance(raw, dict):
            raise BatchManifestError(f"{label} must be an object")
        unknown = sorted(set(raw) - TASK_FIELDS)
        if unknown:
            raise BatchManifestError(f"{label} has unexpected fields: {', '.join(unknown)}")
        goal = str(raw.get("goal", "")).strip()
        if not goal or len(goal) > 20_000:
            raise BatchManifestError(f"{label}.goal must contain 1-20000 characters")
        raw_repo = str(raw.get("repo", "")).strip()
        if not raw_repo:
            raise BatchManifestError(f"{label}.repo is required")
        definition = repositories.get(raw_repo)
        if definition is None:
            candidate = Path(raw_repo).expanduser()
            repository = (
                candidate if candidate.is_absolute() else base_dir / candidate
            ).resolve()
            if not repository.is_dir():
                registered = _registered_projects().get(raw_repo)
                repository = registered or repository
            if not repository.is_dir():
                raise BatchManifestError(f"{label}.repo does not resolve to a project folder")
            definition = {"repository": repository, "max_parallel_tasks": 0}
        parallel = raw.get("parallel", False)
        if not isinstance(parallel, bool):
            raise BatchManifestError(f"{label}.parallel must be true or false")
        mode = str(raw.get("mode", "auto"))
        if mode not in {"auto", "adaptive", "fast", "full", "goal"}:
            raise BatchManifestError(f"{label}.mode is invalid")
        priority = raw.get("priority", 0)
        max_retries = raw.get("max_retries", 2)
        if isinstance(priority, bool) or not isinstance(priority, int) or not -100 <= priority <= 100:
            raise BatchManifestError(f"{label}.priority must be between -100 and 100")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or not 0 <= max_retries <= 10
        ):
            raise BatchManifestError(f"{label}.max_retries must be between 0 and 10")
        task_limit = _positive_limit(
            raw.get("max_parallel_tasks"),
            f"{label}.max_parallel_tasks",
            int(definition["max_parallel_tasks"]),
        )
        normalized.append(
            {
                "repository": Path(definition["repository"]).resolve(),
                "repository_alias": raw_repo,
                "goal": goal,
                "parallel": parallel,
                "task_id": str(raw.get("task_id", "")).strip(),
                "mode": mode,
                "priority": priority,
                "max_retries": max_retries,
                "max_parallel_tasks": task_limit,
                "batch_index": index,
            }
        )
    return normalized
