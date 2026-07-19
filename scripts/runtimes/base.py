"""Stable provider-neutral runtime interface used by the harness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class RuntimeDescriptor:
    provider: str
    kind: str
    transport: str
    production: bool
    command: str
    api_required: bool

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


class Runtime(Protocol):
    """The only LLM execution surface visible to the harness."""

    @property
    def descriptor(self) -> RuntimeDescriptor: ...

    def preflight(self, *, worktree: Path, timeout_seconds: int) -> dict[str, Any]: ...

    def execute(
        self,
        *,
        role: str,
        context: Path,
        task: dict[str, Any],
        worktree: Path,
        artifacts: Path,
    ) -> dict[str, Any]: ...
