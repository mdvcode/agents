"""Atomic role checkpoint storage and resume routing."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CHECKPOINT_STATES = {
    "role_pending",
    "role_running",
    "role_output_received",
    "role_validating",
    "role_completed",
}


class CheckpointError(ValueError):
    pass


@dataclass(frozen=True)
class RoleCheckpoint:
    run_id: str
    role: str
    state: str
    attempt: int
    worktree: str
    input_fingerprint: str = ""
    output_fingerprint: str = ""
    artifacts: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.state not in CHECKPOINT_STATES:
            raise CheckpointError(f"invalid checkpoint state: {self.state}")
        if self.attempt < 1 or not self.run_id or not self.role:
            raise CheckpointError("checkpoint identity and positive attempt are required")

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def _path(run_dir: Path, role: str) -> Path:
    if not role or Path(role).name != role:
        raise CheckpointError(f"unsafe checkpoint role: {role!r}")
    return run_dir.resolve() / "checkpoints" / f"{role}.json"


def write_checkpoint(run_dir: Path, checkpoint: RoleCheckpoint) -> Path:
    path = _path(run_dir, checkpoint.role)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(checkpoint.as_json(), indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return path


def read_checkpoint(run_dir: Path, role: str) -> RoleCheckpoint | None:
    path = _path(run_dir, role)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise CheckpointError("checkpoint must be a JSON object")
        return RoleCheckpoint(**value)
    except (OSError, json.JSONDecodeError, TypeError, CheckpointError) as exc:
        raise CheckpointError(f"corrupted checkpoint {path.name}: {exc}") from exc


def resume_operation(checkpoint: RoleCheckpoint) -> str:
    return {
        "role_pending": "execute_role",
        "role_running": "execute_role",
        "role_output_received": "validate_output",
        "role_validating": "validate_output",
        "role_completed": "next_role",
    }[checkpoint.state]
