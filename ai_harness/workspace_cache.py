"""Shared, non-secret dependency cache environment for isolated task worktrees."""

from __future__ import annotations

import hashlib
from pathlib import Path


def repository_cache_key(repository: Path) -> str:
    name = "".join(character if character.isalnum() else "-" for character in repository.name)
    digest = hashlib.sha256(str(repository.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"{name.strip('-') or 'repository'}-{digest}"


def cache_environment(cache_root: Path, repository: Path) -> dict[str, str]:
    """Create private cache roots and return standard package-manager variables."""

    root = cache_root.resolve()
    shared = root / "shared"
    project = root / "repositories" / repository_cache_key(repository)
    paths = {
        "PIP_CACHE_DIR": shared / "pip",
        "UV_CACHE_DIR": shared / "uv",
        "npm_config_cache": shared / "npm",
        "BUN_INSTALL_CACHE_DIR": shared / "bun",
        "TURBO_CACHE_DIR": project / "turbo",
        "AGENT_BUILD_CACHE_DIR": project / "build",
        "AGENT_VENV_CACHE_DIR": project / "venvs",
        "AGENT_CONTAINER_CACHE_DIR": project / "container-layers",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    return {name: str(path) for name, path in paths.items()}
