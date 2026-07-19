#!/usr/bin/env python3
"""Provider-neutral JSON contracts shared by runtime adapters and the harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
DEFAULT_TIMEOUT_SECONDS = 600


class ContractError(ValueError):
    """Raised when a runtime request or result does not match its contract."""


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ContractError(f"{path.name}: top-level value must be an object")
    return data


def validate_contract(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        return validate_json_schema_object(data, schema, label)
    errors: list[str] = []
    type_map = {"str": str, "bool": bool, "list": list, "dict": dict, "int": int}
    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"{label}: missing required field {field!r}")
    for field, type_name in schema.get("types", {}).items():
        if field not in data:
            continue
        expected = type_map.get(type_name)
        if expected is None:
            errors.append(f"{label}: schema uses unknown type {type_name!r} for {field!r}")
        elif not isinstance(data[field], expected):
            errors.append(f"{label}: field {field!r} must be {type_name}, got {type(data[field]).__name__}")
    for field, allowed in schema.get("enums", {}).items():
        if field in data and data[field] not in allowed:
            errors.append(f"{label}: field {field!r} has invalid value {data[field]!r}")
    return errors


def validate_json_schema_object(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    type_map: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "boolean": bool,
        "array": list,
        "object": dict,
        "integer": int,
        "number": (int, float),
    }
    properties = schema.get("properties", {})
    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"{label}: missing required field {field!r}")
    for field, spec in properties.items():
        if field not in data or not isinstance(spec, dict):
            continue
        type_value = spec.get("type")
        type_names = type_value if isinstance(type_value, list) else [type_value]
        expected_types: tuple[type, ...] = ()
        for type_name in type_names:
            if type_name == "null":
                expected_types += (type(None),)
                continue
            mapped = type_map.get(type_name) if isinstance(type_name, str) else None
            if isinstance(mapped, tuple):
                expected_types += mapped
            elif isinstance(mapped, type):
                expected_types += (mapped,)
        if expected_types and not isinstance(data[field], expected_types):
            errors.append(f"{label}: field {field!r} must be {type_value}, got {type(data[field]).__name__}")
            continue
        if isinstance(data[field], dict) and "object" in type_names:
            errors.extend(validate_json_schema_object(data[field], spec, f"{label}.{field}"))
        allowed = spec.get("enum")
        if isinstance(allowed, list) and data[field] not in allowed:
            errors.append(f"{label}: field {field!r} has invalid value {data[field]!r}")
    if schema.get("additionalProperties") is False:
        for field in sorted(set(data) - set(properties)):
            errors.append(f"{label}: unexpected field {field!r}")
    return errors


def contract_section(schema: dict[str, Any], section: str) -> dict[str, Any]:
    selected = schema.get(section)
    return selected if isinstance(selected, dict) else schema


def resolve_contract_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else ROOT / path


def blocked_result(summary: str, blockers: Sequence[str], warnings: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "status": "blocked",
        "next_action": "blocked",
        "summary": summary,
        "artifacts_created": [],
        "blockers": list(blockers),
        "warnings": list(warnings),
        "tokens_used": 0,
    }
