#!/usr/bin/env python3
"""Validate required agent artifacts and machine-readable policy contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
SCHEMAS = ROOT / "schemas"
POLICY = ROOT / ".agent-policy.yaml"
PROJECT_PROFILES = ROOT / ".agent-project-profiles.yaml"
DEPRECATED_COMBINED_PUBLICATION_KEY = "commit" + "_push"
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


def load_yaml(path: Path, label: str) -> tuple[Any | None, list[str]]:
    if not path.exists():
        return None, [f"{label}: missing"]
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle), []
    except yaml.YAMLError as exc:
        return None, [f"{label}: invalid YAML: {exc}"]


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


def load_and_validate_json_artifact(
    name: str, artifact_path: Path, schema_path: Path
) -> tuple[dict[str, Any] | None, list[str]]:
    data = load_json(artifact_path)
    if not isinstance(data, dict):
        return None, [f"{name}.json: top-level value must be an object"]
    schema = load_json(schema_path)
    return data, validate_required(data, schema, f"{name}.json")


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


def contains_key(value: Any, forbidden_key: str) -> bool:
    if isinstance(value, dict):
        return forbidden_key in value or any(contains_key(item, forbidden_key) for item in value.values())
    if isinstance(value, list):
        return any(contains_key(item, forbidden_key) for item in value)
    return False


def validate_policy_data(policy: Any, label: str = ".agent-policy.yaml") -> list[str]:
    errors: list[str] = []
    if not isinstance(policy, dict):
        return [f"{label}: top-level value must be an object"]
    if contains_key(policy, DEPRECATED_COMBINED_PUBLICATION_KEY):
        errors.append(f"{label}: deprecated combined publication key is not allowed")
    if policy.get("version") != 1:
        errors.append(f"{label}: version must be 1")
    risk_classes = policy.get("risk_classes")
    if not isinstance(risk_classes, dict):
        return errors + [f"{label}: missing object 'risk_classes'"]

    for risk_class in ("low", "medium", "high"):
        rules = risk_classes.get(risk_class)
        if not isinstance(rules, dict):
            errors.append(f"{label}: risk_classes.{risk_class} must be an object")
            continue
        expected_publish = risk_class in {"low", "medium"}
        expected_human = risk_class == "high"
        for field in ("patch", "commit", "push", "open_pr", "update_pr"):
            expected = True if field == "patch" else expected_publish
            if rules.get(field) is not expected:
                errors.append(f"{label}: risk_classes.{risk_class}.{field} must be {expected}")
        if rules.get("require_human_approval") is not expected_human:
            errors.append(
                f"{label}: risk_classes.{risk_class}.require_human_approval must be {expected_human}"
            )
        for field in ("auto_merge", "deploy_staging", "deploy_production"):
            if rules.get(field) is not False:
                errors.append(f"{label}: risk_classes.{risk_class}.{field} must be false")
    projects = policy.get("projects")
    if not isinstance(projects, dict):
        return errors + [f"{label}: missing object 'projects'"]
    flowfox = projects.get("flowfox")
    if not isinstance(flowfox, dict):
        return errors + [f"{label}: projects.flowfox must be an object"]
    publication = flowfox.get("publication")
    if not isinstance(publication, dict):
        errors.append(f"{label}: projects.flowfox.publication must be an object")
    else:
        for risk_class in ("low", "medium", "high"):
            rules = publication.get(risk_class)
            if not isinstance(rules, dict):
                errors.append(f"{label}: projects.flowfox.publication.{risk_class} must be an object")
                continue
            expected = risk_class in {"low", "medium"}
            for field in ("commit", "push", "open_pr", "update_pr"):
                if rules.get(field) is not expected:
                    errors.append(
                        f"{label}: projects.flowfox.publication.{risk_class}.{field} must be {expected}"
                    )
    protected_paths = flowfox.get("protected_paths")
    if not isinstance(protected_paths, list) or not protected_paths:
        errors.append(f"{label}: projects.flowfox.protected_paths must be a non-empty list")
    else:
        required_patterns = ("artifacts/**", ".env", ".env.*", "**/migrations/**")
        for pattern in required_patterns:
            if pattern not in protected_paths:
                errors.append(f"{label}: projects.flowfox.protected_paths missing {pattern!r}")
    evidence = flowfox.get("require_visual_evidence_for")
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{label}: projects.flowfox.require_visual_evidence_for must be a non-empty list")
    return errors


def validate_policy_file(path: Path = POLICY) -> list[str]:
    policy, errors = load_yaml(path, ".agent-policy.yaml")
    if errors:
        return errors
    return validate_policy_data(policy)


def validate_project_profiles_data(
    profiles_doc: Any, label: str = ".agent-project-profiles.yaml"
) -> list[str]:
    errors: list[str] = []
    if not isinstance(profiles_doc, dict):
        return [f"{label}: top-level value must be an object"]
    if profiles_doc.get("version") != 1:
        errors.append(f"{label}: version must be 1")
    profiles = profiles_doc.get("profiles")
    if not isinstance(profiles, dict):
        return errors + [f"{label}: missing object 'profiles'"]
    for profile in ("agent_workspace", "django", "flowfox"):
        value = profiles.get(profile)
        if not isinstance(value, dict):
            errors.append(f"{label}: profiles.{profile} must be an object")
            continue
        for field in ("quality_commands", "security_commands", "test_strategy", "frontend_evidence"):
            if field not in value:
                errors.append(f"{label}: profiles.{profile} missing {field!r}")
    return errors


def validate_project_profiles_file(path: Path = PROJECT_PROFILES) -> list[str]:
    profiles_doc, errors = load_yaml(path, ".agent-project-profiles.yaml")
    if errors:
        return errors
    return validate_project_profiles_data(profiles_doc)


def validate_risk_invariants(risk: dict[str, Any], label: str = "risk.json") -> list[str]:
    errors: list[str] = []
    risk_class = risk.get("risk_class")
    autonomy = risk.get("autonomy_allowed")
    if not isinstance(autonomy, dict):
        return [f"{label}: autonomy_allowed must be an object"]
    if risk_class in {"low", "medium"}:
        for field in ("commit", "push", "open_pr", "update_pr"):
            if autonomy.get(field) is not True:
                errors.append(f"{label}: {risk_class} risk requires autonomy_allowed.{field}=true")
    elif risk_class == "high":
        for field in ("commit", "push", "open_pr", "update_pr"):
            if autonomy.get(field) is not False:
                errors.append(f"{label}: high risk requires autonomy_allowed.{field}=false")
    for field in ("auto_merge", "deploy_staging", "deploy_production"):
        if autonomy.get(field) is not False:
            errors.append(f"{label}: autonomy_allowed.{field} must be false")
    return errors


def validate_verdict_invariants(verdict: dict[str, Any], label: str = "verdict.json") -> list[str]:
    errors: list[str] = []
    decision = verdict.get("decision")
    execution_status = verdict.get("execution_status")
    risk_class = verdict.get("risk_class")
    publication_result = verdict.get("publication_result")
    checks_passed = verdict.get("checks_passed")
    approval_before_publish = verdict.get("approval_required_before_publish")
    visual_evidence = verdict.get("flowfox_visual_evidence")
    high_risk_triggers = verdict.get("high_risk_triggers")
    protected_paths_touched = verdict.get("protected_paths_touched")

    if not isinstance(publication_result, dict):
        return [f"{label}: publication_result must be an object"]
    commit_created = publication_result.get("commit_created")
    branch_pushed = publication_result.get("branch_pushed")
    pr_created = publication_result.get("pr_created_or_updated")
    pr_url = publication_result.get("pr_url")
    pr_state = publication_result.get("pr_state")
    evidence_required = isinstance(visual_evidence, dict) and visual_evidence.get("required") is True
    evidence_provided = isinstance(visual_evidence, dict) and visual_evidence.get("provided") is True

    if branch_pushed is True and commit_created is not True:
        errors.append(f"{label}: branch_pushed=true requires commit_created=true")
    if pr_created is True and branch_pushed is not True:
        errors.append(f"{label}: pr_created_or_updated=true requires branch_pushed=true")
    if pr_created is True and pr_state == "not_created":
        errors.append(f"{label}: pr_created_or_updated=true cannot use pr_state=not_created")
    if risk_class == "high" and decision not in {"await_approval", "reject"}:
        errors.append(f"{label}: high risk must use await_approval or reject")
    if risk_class == "high" and any((commit_created, branch_pushed, pr_created)):
        errors.append(f"{label}: high risk must not create commits, push branches, or publish PRs")
    if high_risk_triggers:
        if decision not in {"await_approval", "reject"}:
            errors.append(f"{label}: high_risk_triggers require await_approval or reject")
        if any((commit_created, branch_pushed, pr_created)):
            errors.append(f"{label}: high_risk_triggers block publication_result actions")
    if protected_paths_touched:
        if any((commit_created, branch_pushed, pr_created)):
            errors.append(f"{label}: protected_paths_touched block publication_result actions")
    if decision == "publish_pr" and risk_class == "high":
        errors.append(f"{label}: publish_pr is not allowed for high risk")
    if decision == "await_approval" and approval_before_publish is not True:
        errors.append(f"{label}: await_approval requires approval_required_before_publish=true")
    if decision == "publish_pr" and execution_status == "completed" and pr_created is not True:
        errors.append(f"{label}: completed publish_pr requires pr_created_or_updated=true")
    if decision == "publish_pr" and execution_status == "completed" and not pr_url:
        errors.append(f"{label}: completed publish_pr requires pr_url")
    if pr_created is True and not pr_url:
        errors.append(f"{label}: pr_created_or_updated=true requires pr_url")
    if pr_state == "ready" and checks_passed is not True:
        errors.append(f"{label}: pr_state=ready requires checks_passed=true")
    if pr_state == "ready" and evidence_required and not evidence_provided:
        errors.append(f"{label}: pr_state=ready requires required visual evidence")
    if checks_passed is False and pr_created is True and pr_state != "draft":
        errors.append(f"{label}: failed checks with an existing PR require pr_state=draft")
    if evidence_required and not evidence_provided and pr_created is True and pr_state != "draft":
        errors.append(f"{label}: missing required visual evidence with an existing PR requires pr_state=draft")
    return errors


def validate_cross_artifact_invariants(artifacts: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    project_profile = artifacts.get("project_profile", {}).get("project_profile")
    quality_profile = artifacts.get("quality", {}).get("project_profile")
    verdict_profile = artifacts.get("verdict", {}).get("project_profile")
    if len({project_profile, quality_profile, verdict_profile}) != 1:
        errors.append(
            "project profile mismatch across project_profile.json, quality.json, and verdict.json"
        )
    risk_class = artifacts.get("risk", {}).get("risk_class")
    verdict_risk_class = artifacts.get("verdict", {}).get("risk_class")
    if risk_class != verdict_risk_class:
        errors.append("risk_class mismatch between risk.json and verdict.json")
    return errors


def main() -> int:
    errors: list[str] = []
    loaded_artifacts: dict[str, dict[str, Any]] = {}
    for name, (artifact_path, schema_path) in JSON_ARTIFACTS.items():
        data, artifact_errors = load_and_validate_json_artifact(name, artifact_path, schema_path)
        errors.extend(artifact_errors)
        if data is not None:
            loaded_artifacts[name] = data

    errors.extend(validate_audit_log())
    errors.extend(validate_policy_file())
    errors.extend(validate_project_profiles_file())

    risk = loaded_artifacts.get("risk")
    verdict = loaded_artifacts.get("verdict")
    if risk is not None:
        errors.extend(validate_risk_invariants(risk))
    if verdict is not None:
        errors.extend(validate_verdict_invariants(verdict))
    if {"risk", "quality", "verdict", "project_profile"}.issubset(loaded_artifacts):
        errors.extend(validate_cross_artifact_invariants(loaded_artifacts))

    if errors:
        for error in errors:
            print(error)
        return 1

    print("artifact validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
