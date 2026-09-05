"""Structured provenance logs for context builds."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Protocol

from .models import Context
from .content_guard import redact_value


class ContextLogger(Protocol):
    def write(self, event: Mapping[str, object]) -> str: ...


class JsonlContextLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: Mapping[str, object]) -> str:
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError("refusing to write context log through a symbolic link")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(redact_value(dict(event)), ensure_ascii=False, sort_keys=True) + "\n")
        return str(self.path.resolve())


def attach_log(context: Context, event: Mapping[str, object], logger: ContextLogger | None) -> Context:
    log_path = logger.write(event) if logger is not None else ""
    return replace(context, log=dict(event), log_path=log_path)
