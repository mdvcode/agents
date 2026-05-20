#!/usr/bin/env python3
"""Validate required agent artifacts with a tiny standard-library checker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
SCHEMAS = ROOT / "schemas"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_required(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"{label}: missing required field {field!r}")
    for field, allowed in schema.get("enums", {}).items():
        if field in data and data[field] not in allowed:
            errors.append(f"{label}: field {field!r} has invalid value {data[field]!r}")
    return errors


def validate_json_artifact(name: str) -> list[str]:
    data = load_json(ARTIFACTS / f"{name}.json")
    if not isinstance(data, dict):
        return [f"{name}.json: top-level value must be an object"]
    schema = load_json(SCHEMAS / f"{name}.schema.json")
    return validate_required(data, schema, f"{name}.json")


def validate_audit_log() -> list[str]:
    errors: list[str] = []
    path = ARTIFACTS / "audit_log.jsonl"
    if not path.exists():
        return ["audit_log.jsonl: missing"]
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as exc:
                errors.append(f"audit_log.jsonl:{line_number}: invalid JSON: {exc}")
                continue
            for field in ("time", "agent", "action", "verdict", "checks_passed"):
                if field not in entry:
                    errors.append(f"audit_log.jsonl:{line_number}: missing {field!r}")
    return errors


def main() -> int:
    errors: list[str] = []
    for name in ("risk", "quality", "verdict"):
        errors.extend(validate_json_artifact(name))
    errors.extend(validate_audit_log())

    if errors:
        for error in errors:
            print(error)
        return 1

    print("artifact validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
