"""Project-local configuration used by the user-facing CLI."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_RELATIVE_PATH = Path(".agent/project.yaml")
SUPPORTED_PROFILES = {"agent_workspace", "django", "nextjs_web"}
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class ProjectConfigError(ValueError):
    """Raised when `.agent/project.yaml` is missing or invalid."""


@dataclass(frozen=True)
class ProjectConfig:
    repository: Path
    project_id: str
    profile: str
    base_branch: str
    branch_prefix: str
    runtime_provider: str

    @property
    def path(self) -> Path:
        return self.repository / CONFIG_RELATIVE_PATH

    def as_document(self) -> dict[str, Any]:
        return {
            "version": 1,
            "project": {
                "id": self.project_id,
                "profile": self.profile,
                "repository": ".",
                "base_branch": self.base_branch,
                "branch_prefix": self.branch_prefix,
            },
            "runtime": {"provider": self.runtime_provider},
        }


def slug(value: str, fallback: str = "project") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:64] or fallback


def safe_branch(value: str) -> bool:
    if not value or len(value) > 255 or not BRANCH_PATTERN.fullmatch(value):
        return False
    if ".." in value or "@{" in value or "//" in value or value.endswith(("/", ".")):
        return False
    components = value.split("/")
    return all(
        component
        and not component.startswith(".")
        and not component.endswith((".", ".lock"))
        for component in components
    )


def safe_branch_prefix(value: str) -> bool:
    """Return whether a configurable prefix can form a safe task branch."""

    return bool(value and len(value) <= 128 and safe_branch(f"{value}task"))


def detect_profile(repository: Path) -> str:
    if (repository / "manage.py").is_file() or (repository / "settings.py").is_file():
        return "django"
    if any((repository / marker).exists() for marker in ("package.json", "next.config.js", "next.config.ts")):
        return "nextjs_web"
    if (repository / ".agent-policy.yaml").is_file() and (repository / ".agents").is_dir():
        return "agent_workspace"
    return "agent_workspace"


def git_output(repository: Path, arguments: list[str]) -> str:
    """Return one successful git result without leaking diagnostics to the CLI."""

    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def detect_base_branch(repository: Path) -> str:
    """Choose the repository's existing default branch, with a safe fallback."""

    remote_head = git_output(repository, ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"])
    if remote_head.startswith("origin/"):
        candidate = remote_head.removeprefix("origin/")
        if safe_branch(candidate):
            return candidate
    for candidate in ("main", "master", "trunk"):
        if git_output(repository, ["show-ref", "--verify", f"refs/remotes/origin/{candidate}"]):
            return candidate
        if git_output(repository, ["show-ref", "--verify", f"refs/heads/{candidate}"]):
            return candidate
    current = git_output(repository, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    return current if safe_branch(current) else "main"


def discover_repository(start: Path, explicit: bool = False) -> Path:
    candidate = start.expanduser().resolve()
    if not candidate.is_dir():
        raise ProjectConfigError(f"repository does not exist: {candidate}")
    if explicit:
        return candidate
    for path in (candidate, *candidate.parents):
        if (path / CONFIG_RELATIVE_PATH).is_file():
            return path
    return candidate


def default_config(
    repository: Path,
    *,
    project_id: str = "",
    profile: str = "auto",
    base_branch: str = "auto",
    branch_prefix: str = "feat/",
) -> ProjectConfig:
    selected_profile = detect_profile(repository) if profile == "auto" else profile
    selected_base_branch = detect_base_branch(repository) if base_branch == "auto" else base_branch.strip()
    return ProjectConfig(
        repository=repository.resolve(),
        project_id=slug(project_id or repository.name),
        profile=selected_profile,
        base_branch=selected_base_branch,
        branch_prefix=branch_prefix.strip(),
        runtime_provider="codex-cli",
    )


def validate_config(config: ProjectConfig) -> list[str]:
    errors: list[str] = []
    if not config.project_id or slug(config.project_id) != config.project_id:
        errors.append("project.id must be a lowercase slug")
    if config.profile not in SUPPORTED_PROFILES:
        errors.append(f"project.profile must be one of {sorted(SUPPORTED_PROFILES)}")
    if not safe_branch(config.base_branch):
        errors.append("project.base_branch must be a safe git branch name")
    if not safe_branch_prefix(config.branch_prefix):
        errors.append("project.branch_prefix must form a safe Git branch name")
    if config.runtime_provider != "codex-cli":
        errors.append("runtime.provider must be codex-cli in Step 2")
    return errors


def load_project_config(repository: Path) -> ProjectConfig:
    path = repository.resolve() / CONFIG_RELATIVE_PATH
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectConfigError(f"{path} is missing; run `agent init --repo {repository}`") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ProjectConfigError(f"{path}: version must be 1")
    if set(document) != {"version", "project", "runtime"}:
        raise ProjectConfigError(f"{path}: unexpected top-level fields")
    project = document.get("project")
    runtime = document.get("runtime")
    if not isinstance(project, dict) or not isinstance(runtime, dict):
        raise ProjectConfigError(f"{path}: project and runtime objects are required")
    if set(project) != {"id", "profile", "repository", "base_branch", "branch_prefix"}:
        raise ProjectConfigError(f"{path}: unexpected project fields")
    if set(runtime) != {"provider"}:
        raise ProjectConfigError(f"{path}: unexpected runtime fields")
    if project.get("repository") != ".":
        raise ProjectConfigError(f"{path}: project.repository must be '.'")
    config = ProjectConfig(
        repository=repository.resolve(),
        project_id=str(project.get("id", "")),
        profile=str(project.get("profile", "")),
        base_branch=str(project.get("base_branch", "")),
        branch_prefix=str(project.get("branch_prefix", "")),
        runtime_provider=str(runtime.get("provider", "")),
    )
    errors = validate_config(config)
    if errors:
        raise ProjectConfigError(f"{path}: " + "; ".join(errors))
    return config


def write_project_config(config: ProjectConfig, *, force: bool = False) -> bool:
    errors = validate_config(config)
    if errors:
        raise ProjectConfigError("; ".join(errors))
    path = config.path
    if path.parent.is_symlink() or path.is_symlink():
        raise ProjectConfigError("refusing to write project config through a symbolic link")
    if path.exists() and not force:
        load_project_config(config.repository)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config.as_document(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return True


def local_trust_path() -> Path:
    configured = os.environ.get("AI_HARNESS_CONFIG_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".config" / "ai-harness"
    return root.resolve() / "projects.yaml"


def config_fingerprint(config: ProjectConfig) -> str:
    payload = yaml.safe_dump(config.as_document(), sort_keys=True, allow_unicode=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def trust_key(repository: Path) -> str:
    return hashlib.sha256(str(repository.resolve()).encode("utf-8")).hexdigest()


def load_local_trust(path: Path | None = None) -> dict[str, Any]:
    selected = path or local_trust_path()
    if not selected.is_file():
        return {"version": 1, "projects": {}}
    try:
        document = yaml.safe_load(selected.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectConfigError(f"cannot read local project trust: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ProjectConfigError("local project trust must contain version: 1")
    projects = document.get("projects")
    if not isinstance(projects, dict):
        raise ProjectConfigError("local project trust must contain projects object")
    return document


def register_local_project(config: ProjectConfig, path: Path | None = None) -> Path:
    selected = path or local_trust_path()
    if selected.is_symlink() or selected.parent.is_symlink():
        raise ProjectConfigError("refusing to write local project trust through a symbolic link")
    document = load_local_trust(selected)
    projects = document["projects"]
    projects[trust_key(config.repository)] = {
        "repository": str(config.repository.resolve()),
        "project_id": config.project_id,
        "profile": config.profile,
        "config_fingerprint": config_fingerprint(config),
    }
    selected.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = selected.with_name(f".{selected.name}.{os.getpid()}.tmp")
    temporary.write_text(
        yaml.safe_dump(document, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(selected)
    return selected


def project_is_trusted(config: ProjectConfig, path: Path | None = None) -> bool:
    document = load_local_trust(path)
    entry = document["projects"].get(trust_key(config.repository))
    return bool(
        isinstance(entry, dict)
        and entry.get("repository") == str(config.repository.resolve())
        and entry.get("project_id") == config.project_id
        and entry.get("profile") == config.profile
        and entry.get("config_fingerprint") == config_fingerprint(config)
    )
