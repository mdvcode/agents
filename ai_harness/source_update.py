"""Preserve private control-plane knowledge during a source fast-forward."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

PRIVATE_PREFIXES = (
    "docs/memory/",
    "docs/projects/",
    "docs/wiki/",
    "docs/graph/",
    "docs/kanban/",
)
PRIVATE_FILES = {".agent-repositories.yaml"}
BUILD_PREFIXES = (
    "ai_harness/",
    "scripts/",
    "schemas/",
    ".agents/",
    "packaging/",
    "docs/templates/",
    "evals/",
)
BUILD_FILES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "MANIFEST.in",
    "AGENTS.md",
    "Makefile",
    "install.sh",
    "docs/index.md",
}


class SourceStateError(ValueError):
    """Local code or redirected memory needs review before updating."""


def inspect_source_changes(source: Path, runner: Callable[..., Any]) -> list[str]:
    result = runner(
        [
            "git",
            "-C",
            str(source),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=normal",
        ],
        label="checking the downloaded system",
        timeout=30,
    )
    entries = (
        result.stdout.split("\0")
        if "\0" in result.stdout
        else result.stdout.splitlines()
    )
    memory: list[str] = []
    blocked: list[str] = []
    skip_destination = False
    for entry in entries:
        if skip_destination:
            skip_destination = False
            continue
        if not entry:
            continue
        status_code, relative = entry[:2], entry[3:]
        if "R" in status_code or "C" in status_code:
            skip_destination = True
        path = Path(relative.rstrip("/"))
        if path.is_absolute() or ".." in path.parts:
            raise SourceStateError("source status contains an unsafe path")
        if status_code == "??":
            # Untracked projects, editor files, caches and outputs are not
            # package inputs. Git itself refuses a pull that would overwrite them.
            if (
                relative.startswith(BUILD_PREFIXES)
                or relative in BUILD_FILES
                or relative.startswith(".agent-")
            ):
                blocked.append(relative)
        elif status_code in {" M", " D"} and (
            relative.startswith(PRIVATE_PREFIXES) or relative in PRIVATE_FILES
        ):
            memory.append(relative)
        else:
            blocked.append(relative)
    if blocked:
        raise SourceStateError(
            "update stopped because the downloaded system has local source or staged changes: "
            + ", ".join(blocked[:5])
            + "; commit or stash these reviewed code changes before updating; local memory and non-build folders are preserved automatically"
        )
    return memory


def _safe_memory_path(source: Path, relative: str) -> Path:
    path = source / relative
    if any(
        parent.is_symlink()
        for parent in (path, *path.parents)
        if parent != source and source in parent.parents
    ):
        raise SourceStateError(
            f"refusing to update redirected local memory: {relative}"
        )
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise SourceStateError(
            f"local memory must be a regular private file: {relative}"
        )
    return path


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        output.write(content)


@contextmanager
def preserved_source_state(
    source: Path, paths: list[str], runner: Callable[..., Any]
) -> Iterator[Path | None]:
    """Back up before Git writes; restore original knowledge on success or failure.

    Memory is a private overlay, not installed package code. If upstream also
    changes it, keep the new upstream copy in the same private backup for review.
    Staged changes, source edits, symlinks and hard links never enter this path.
    """
    if not paths:
        yield None
        return
    tag = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    run_root = source / ".agent-runs"
    if run_root.is_symlink():
        raise SourceStateError("update backup directory must not be a symbolic link")
    backup = run_root / f"update-{tag}"
    backup.mkdir(parents=True, mode=0o700)
    originals: dict[str, tuple[bytes | None, int]] = {}
    for relative in paths:
        path = _safe_memory_path(source, relative)
        content = path.read_bytes() if path.exists() else None
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        originals[relative] = content, mode
        if content is not None:
            _write_private(backup / "local" / relative, content)
    _write_private(
        backup / "manifest.json",
        json.dumps(
            {
                "paths": paths,
                "deleted": [
                    name for name, (content, _) in originals.items() if content is None
                ],
            },
            indent=2,
        ).encode(),
    )
    cleared = False
    try:
        # Only unstaged knowledge files are restored to HEAD temporarily. Never
        # stash the entire home or traverse nested repositories/caches.
        cleared = True
        runner(
            ["git", "-C", str(source), "restore", "--worktree", "--", *paths],
            label="preparing local memory for update",
            timeout=30,
        )
        yield backup
    finally:
        if cleared:
            for relative, (content, mode) in originals.items():
                path = _safe_memory_path(source, relative)
                if path.exists():
                    _write_private(backup / "upstream" / relative, path.read_bytes())
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_name(f".{path.name}.update-{tag}")
                    _write_private(temporary, content)
                    os.chmod(temporary, mode)
                    temporary.replace(path)
