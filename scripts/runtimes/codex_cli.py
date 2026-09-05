"""Codex CLI runtime provider; local subscription transport, no API calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from check_codex_runtime import check_codex_runtime
from runtimes.base import RuntimeDescriptor
from runtimes.subprocess_runtime import SubprocessRuntime


class CodexCliRuntime(SubprocessRuntime):
    def __init__(self, *, command: str, timeout_seconds: int, raw_output_dir: Path | None) -> None:
        super().__init__(
            descriptor=RuntimeDescriptor(
                provider="codex-cli",
                kind="runtime_adapter",
                transport="local_subscription",
                production=True,
                command=command,
                api_required=False,
                capabilities=("text",),
            ),
            timeout_seconds=timeout_seconds,
            raw_output_dir=raw_output_dir,
        )

    def preflight(self, *, worktree: Path, timeout_seconds: int) -> dict[str, Any]:
        return check_codex_runtime(
            repo=worktree,
            sandbox="read-only",
            timeout_seconds=min(timeout_seconds, 60),
        )
