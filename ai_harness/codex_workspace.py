"""Supported local workspace handoff to the Codex desktop application."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path


class CodexWorkspaceError(RuntimeError):
    """Raised when a trusted workspace cannot be opened in Codex."""


def codex_base_command(configured: str = "") -> list[str]:
    """Resolve the installed Codex command without invoking a shell."""

    selected = configured.strip() or os.environ.get(
        "AGENT_CODEX_CLI_COMMAND", ""
    ).strip()
    if selected:
        try:
            parts = shlex.split(selected)
        except ValueError as exc:
            raise CodexWorkspaceError("configured Codex command is invalid") from exc
        if not parts:
            raise CodexWorkspaceError("configured Codex command is empty")
        executable = parts[0]
        executable_path = Path(executable).expanduser()
        if "/" in executable:
            available = executable_path.is_file() and os.access(
                executable_path, os.X_OK
            )
            if available:
                parts[0] = str(executable_path.resolve())
        else:
            available = shutil.which(executable) is not None
        if not available:
            raise CodexWorkspaceError("configured Codex command is unavailable")
        return parts

    candidates = (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]
    discovered = shutil.which("codex")
    if discovered:
        return [discovered]
    raise CodexWorkspaceError("Codex is not installed or is not executable")


def codex_workspace_command(
    repository: Path, *, configured: str = ""
) -> list[str]:
    """Build the supported desktop workspace command for an existing folder."""

    resolved = repository.expanduser().resolve()
    if not resolved.is_dir():
        raise CodexWorkspaceError("project folder does not exist")
    return [*codex_base_command(configured), "app", str(resolved)]


def open_codex_workspace(
    repository: Path, *, configured: str = ""
) -> None:
    """Open ``repository`` in Codex and return without owning the app process.

    Trust validation intentionally belongs to the caller because the same helper
    is useful to both the authenticated control plane and a future CLI command.
    """

    command = codex_workspace_command(repository, configured=configured)
    resolved = repository.expanduser().resolve()
    try:
        subprocess.Popen(  # noqa: S603 - executable is resolved without a shell
            command,
            cwd=resolved,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise CodexWorkspaceError("Codex could not be launched") from exc
