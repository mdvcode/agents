from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_harness.codex_workspace import (
    CodexWorkspaceError,
    codex_base_command,
    codex_workspace_command,
    open_codex_workspace,
)


def executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_workspace_command_preserves_configured_arguments_without_shell(
    tmp_path: Path,
) -> None:
    codex = executable(tmp_path / "codex helper")
    repository = tmp_path / "project"
    repository.mkdir()

    command = codex_workspace_command(
        repository,
        configured=f"{str(codex)!r} --config feature=true",
    )

    assert command == [
        str(codex.resolve()),
        "--config",
        "feature=true",
        "app",
        str(repository.resolve()),
    ]


def test_workspace_command_rejects_missing_folder(tmp_path: Path) -> None:
    codex = executable(tmp_path / "codex")

    with pytest.raises(CodexWorkspaceError, match="project folder does not exist"):
        codex_workspace_command(tmp_path / "missing", configured=str(codex))


def test_configured_command_rejects_unavailable_executable(tmp_path: Path) -> None:
    with pytest.raises(CodexWorkspaceError, match="unavailable"):
        codex_base_command(str(tmp_path / "missing-codex"))


def test_open_workspace_uses_detached_process_without_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = executable(tmp_path / "codex")
    repository = tmp_path / "project"
    repository.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "Popen", popen)

    open_codex_workspace(repository, configured=str(codex))

    assert calls == [
        (
            [str(codex.resolve()), "app", str(repository.resolve())],
            {
                "cwd": repository.resolve(),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "start_new_session": True,
                "close_fds": True,
            },
        )
    ]
