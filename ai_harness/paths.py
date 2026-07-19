"""Locate the Harness control plane in a source checkout or pipx environment."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class HarnessNotFoundError(RuntimeError):
    """Raised when the installed CLI cannot find its bundled Harness resources."""


def is_harness_home(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / ".agent-runtime.yaml").is_file()
        and (path / "scripts" / "task_queue.py").is_file()
        and (path / "schemas" / "task_envelope.schema.json").is_file()
    )


def harness_home() -> Path:
    configured = os.environ.get("AI_HARNESS_HOME", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(__file__).resolve().parents[1],
        Path(sys.prefix) / "share" / "ai-harness",
    ]
    for candidate in candidates:
        if candidate is not None and is_harness_home(candidate.resolve()):
            return candidate.resolve()
    checked = ", ".join(str(path) for path in candidates if path is not None)
    raise HarnessNotFoundError(
        f"Harness resources were not found. Checked: {checked}. "
        "Reinstall ai-harness or set AI_HARNESS_HOME."
    )
