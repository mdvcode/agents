"""Safe structured I/O shared by evaluation commands."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EvaluationInputError(ValueError):
    """Raised when an evaluation input violates its public contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_object(path: Path, *, required: bool = True) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise EvaluationInputError(f"JSON object does not exist: {path}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationInputError(f"Expected a JSON object in {path}")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_fields(value: dict[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise EvaluationInputError(f"{label} is missing required fields: {', '.join(missing)}")
