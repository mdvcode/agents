#!/usr/bin/env python3
"""Validate required agent artifacts with a tiny standard-library checker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
SCHEMAS = ROOT / "schemas"
POLICY = ROOT / ".agent-policy.yaml"
PROJECT_PROFILES = ROOT / ".agent-project-profiles.yaml"
JSON_ARTIFACTS = {
    "risk": (ARTIFACTS / "risk.json", SCHEMAS / "risk.schema.json"),
    "quality": (ARTIFACTS / "quality.json", SCHEMAS / "quality.schema.json"),
    "verdict": (ARTIFACTS / "verdict.json", SCHEMAS / "verdict.schema.json"),
    "project_profile": (
        ARTIFACTS / "project_profile.json",
        SCHEMAS / "project_profile.schema.json",
    ),
}


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
    errors.extend(validate_types(data, schema, label))
    errors.extend(validate_object_required(data, schema, label))
    return errors


def validate_types(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    type_map = {
        "str": str,
        "bool": bool,
        "list": list,
        "dict": dict,
    }
    for field, type_name in schema.get("types", {}).items():
        if field not in data:
            continue
        expected_type = type_map.get(type_name)
        if expected_type is None:
            errors.append(f"{label}: schema uses unknown type {type_name!r} for {field!r}")
            continue
        if not isinstance(data[field], expected_type):
            actual_type = type(data[field]).__name__
            errors.append(f"{label}: field {field!r} must be {type_name}, got {actual_type}")
    return errors


def validate_object_required(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    for field, required_children in schema.get("object_required", {}).items():
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, dict):
            errors.append(f"{label}: field {field!r} must be an object")
            continue
        for child in required_children:
            if child not in value:
                errors.append(f"{label}: field {field!r} missing child {child!r}")
    return errors


def validate_json_artifact(name: str, artifact_path: Path, schema_path: Path) -> list[str]:
    data = load_json(artifact_path)
    if not isinstance(data, dict):
        return [f"{name}.json: top-level value must be an object"]
    schema = load_json(schema_path)
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


def validate_policy_file() -> list[str]:
    if not POLICY.exists():
        return [".agent-policy.yaml: missing"]
    text = POLICY.read_text(encoding="utf-8")
    errors: list[str] = []
    for marker in ("version:", "default:", "risk_classes:", "projects:", "flowfox:"):
        if marker not in text:
            errors.append(f".agent-policy.yaml: missing marker {marker!r}")
    return errors


def validate_project_profiles_file() -> list[str]:
    if not PROJECT_PROFILES.exists():
        return ["missing .agent-project-profiles.yaml"]
    text = PROJECT_PROFILES.read_text(encoding="utf-8")
    errors: list[str] = []
    required_markers = [
        "version:",
        "profiles:",
        "agent_workspace:",
        "django:",
        "flowfox:",
        "quality_commands:",
        "security_commands:",
        "frontend_evidence:",
    ]
    for marker in required_markers:
        if marker not in text:
            errors.append(f".agent-project-profiles.yaml missing marker: {marker}")
    return errors


def main() -> int:
    errors: list[str] = []
    for name, (artifact_path, schema_path) in JSON_ARTIFACTS.items():
        errors.extend(validate_json_artifact(name, artifact_path, schema_path))
    errors.extend(validate_audit_log())
    errors.extend(validate_policy_file())
    errors.extend(validate_project_profiles_file())

    if errors:
        for error in errors:
            print(error)
        return 1

    print("artifact validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
