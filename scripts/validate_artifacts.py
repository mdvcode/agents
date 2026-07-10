#!/usr/bin/env python3
"""Validate required agent artifacts and machine-readable policy contracts."""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from repository_registry import validate_registry_data


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
SCHEMAS = ROOT / "schemas"
POLICY = ROOT / ".agent-policy.yaml"
PROJECT_PROFILES = ROOT / ".agent-project-profiles.yaml"
AGENT_WORKFLOWS = ROOT / ".agent-workflows.yaml"
AGENT_REPOSITORIES = ROOT / ".agent-repositories.yaml"
DEPRECATED_COMBINED_PUBLICATION_KEY = "commit" + "_push"
JSON_ARTIFACTS = {
    "risk": (ARTIFACTS / "risk.json", SCHEMAS / "risk.schema.json"),
    "quality": (ARTIFACTS / "quality.json", SCHEMAS / "quality.schema.json"),
    "verdict": (ARTIFACTS / "verdict.json", SCHEMAS / "verdict.schema.json"),
    "project_profile": (
        ARTIFACTS / "project_profile.json",
        SCHEMAS / "project_profile.schema.json",
    ),
    "change_set": (ARTIFACTS / "change_set.json", SCHEMAS / "change_set.schema.json"),
    "publication": (ARTIFACTS / "publication.json", SCHEMAS / "publication.schema.json"),
    "publication_payload": (
        ARTIFACTS / "publication_payload.json",
        SCHEMAS / "publication_payload.schema.json",
    ),
}


def json_artifacts(artifacts_dir: Path = ARTIFACTS) -> dict[str, tuple[Path, Path]]:
    return {
        name: (artifacts_dir / artifact_path.name, schema_path)
        for name, (artifact_path, schema_path) in JSON_ARTIFACTS.items()
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
    errors.extend(validate_object_types(data, schema, label))
    errors.extend(validate_object_enums(data, schema, label))
    return errors


def validate_types(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    type_map = {
        "str": str,
        "bool": bool,
        "list": list,
        "dict": dict,
        "int": int,
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


def validate_object_types(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    type_map = {
        "str": str,
        "bool": bool,
        "list": list,
        "dict": dict,
        "int": int,
    }
    for field, children in schema.get("object_types", {}).items():
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, dict):
            errors.append(f"{label}: field {field!r} must be an object")
            continue
        for child, type_name in children.items():
            if child not in value:
                continue
            expected_type = type_map.get(type_name)
            if expected_type is None:
                errors.append(
                    f"{label}: schema uses unknown type {type_name!r} for {field}.{child}"
                )
                continue
            if not isinstance(value[child], expected_type):
                actual_type = type(value[child]).__name__
                errors.append(
                    f"{label}: field {field}.{child!r} must be {type_name}, got {actual_type}"
                )
    return errors


def validate_object_enums(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    for dotted_field, allowed in schema.get("object_enums", {}).items():
        parent, _, child = dotted_field.partition(".")
        if not parent or not child:
            errors.append(f"{label}: invalid object enum path {dotted_field!r}")
            continue
        value = data.get(parent)
        if not isinstance(value, dict) or child not in value:
            continue
        if value[child] not in allowed:
            errors.append(
                f"{label}: field {parent}.{child!r} has invalid value {value[child]!r}"
            )
    return errors


def load_and_validate_json_artifact(
    name: str, artifact_path: Path, schema_path: Path
) -> tuple[dict[str, Any] | None, list[str]]:
    data = load_json(artifact_path)
    if not isinstance(data, dict):
        return None, [f"{name}.json: top-level value must be an object"]
    schema = load_json(schema_path)
    return data, validate_required(data, schema, f"{name}.json")


def validate_audit_log(artifacts_dir: Path = ARTIFACTS) -> list[str]:
    errors: list[str] = []
    path = artifacts_dir / "audit_log.jsonl"
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
    web_project = projects.get("nextjs_web")
    if not isinstance(web_project, dict):
        return errors + [f"{label}: projects.nextjs_web must be an object"]
    publication = web_project.get("publication")
    if not isinstance(publication, dict):
        errors.append(f"{label}: projects.nextjs_web.publication must be an object")
    else:
        allowed_prefixes = publication.get("allowed_branch_prefixes")
        expected_prefixes = ["feat/", "fix/", "issue/", "tast/"]
        if allowed_prefixes != expected_prefixes:
            errors.append(
                f"{label}: projects.nextjs_web.publication.allowed_branch_prefixes must be {expected_prefixes!r}"
            )
        for risk_class in ("low", "medium", "high"):
            rules = publication.get(risk_class)
            if not isinstance(rules, dict):
                errors.append(f"{label}: projects.nextjs_web.publication.{risk_class} must be an object")
                continue
            expected = risk_class in {"low", "medium"}
            for field in ("commit", "push", "open_pr", "update_pr"):
                if rules.get(field) is not expected:
                    errors.append(
                        f"{label}: projects.nextjs_web.publication.{risk_class}.{field} must be {expected}"
                    )
    protected_paths = web_project.get("protected_paths")
    if not isinstance(protected_paths, list) or not protected_paths:
        errors.append(f"{label}: projects.nextjs_web.protected_paths must be a non-empty list")
    else:
        required_patterns = ("artifacts/**", ".env", ".env.*", "**/migrations/**")
        for pattern in required_patterns:
            if pattern not in protected_paths:
                errors.append(f"{label}: projects.nextjs_web.protected_paths missing {pattern!r}")
    evidence = web_project.get("require_visual_evidence_for")
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{label}: projects.nextjs_web.require_visual_evidence_for must be a non-empty list")
    evidence_policy = web_project.get("visual_evidence_policy")
    if not isinstance(evidence_policy, dict):
        errors.append(f"{label}: projects.nextjs_web.visual_evidence_policy must be an object")
    else:
        if evidence_policy.get("ready_pr_requires_evidence") is not True:
            errors.append(
                f"{label}: projects.nextjs_web.visual_evidence_policy.ready_pr_requires_evidence must be true"
            )
        if evidence_policy.get("missing_evidence_creates_draft_pr") is not True:
            errors.append(
                f"{label}: projects.nextjs_web.visual_evidence_policy.missing_evidence_creates_draft_pr must be true"
            )
    forbidden_phrases = web_project.get("public_output_forbidden_phrases")
    if not isinstance(forbidden_phrases, list) or not forbidden_phrases:
        errors.append(f"{label}: projects.nextjs_web.public_output_forbidden_phrases must be a non-empty list")
    if "AI" in forbidden_phrases:
        errors.append(f"{label}: projects.nextjs_web.public_output_forbidden_phrases must not ban the product term 'AI'")
    applies_to = web_project.get("public_output_filter_applies_to")
    if not isinstance(applies_to, list) or not applies_to:
        errors.append(f"{label}: projects.nextjs_web.public_output_filter_applies_to must be a non-empty list")
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
    for profile in ("agent_workspace", "django", "nextjs_web"):
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


def validate_agent_workflows_data(
    workflows_doc: Any, label: str = ".agent-workflows.yaml"
) -> list[str]:
    errors: list[str] = []
    if not isinstance(workflows_doc, dict):
        return [f"{label}: top-level value must be an object"]
    if workflows_doc.get("version") != 1:
        errors.append(f"{label}: version must be 1")
    workflows = workflows_doc.get("workflows")
    if not isinstance(workflows, dict):
        return errors + [f"{label}: missing object 'workflows'"]
    publish_pr = workflows.get("publish_pr")
    if not isinstance(publish_pr, dict):
        return errors + [f"{label}: workflows.publish_pr must be an object"]
    if publish_pr.get("executor") != "python3 scripts/publish_pr.py":
        errors.append(f"{label}: workflows.publish_pr.executor must be 'python3 scripts/publish_pr.py'")
    full = workflows.get("full_agent_workflow")
    if not isinstance(full, dict):
        errors.append(f"{label}: workflows.full_agent_workflow must be an object")
    else:
        executor = full.get("executor")
        if not isinstance(executor, str) or "--run-id {run_id}" not in executor:
            errors.append(f"{label}: workflows.full_agent_workflow.executor must pass the shared run id")
        if not isinstance(executor, str) or "--artifacts-dir {artifacts_dir}" not in executor:
            errors.append(f"{label}: workflows.full_agent_workflow.executor must pass the run-scoped artifacts dir")
        if not isinstance(executor, str) or "--create-worktree" not in executor:
            errors.append(f"{label}: workflows.full_agent_workflow.executor must create a task worktree")
    mutation_rules = publish_pr.get("mutation_rules")
    if not isinstance(mutation_rules, list) or not any("git add -A" in rule for rule in mutation_rules):
        errors.append(f"{label}: workflows.publish_pr.mutation_rules must forbid git add -A")
    return errors


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
    visual_evidence = verdict.get("visual_evidence")
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
    publication = artifacts.get("publication")
    verdict = artifacts.get("verdict")
    if publication is not None and verdict is not None:
        publication_result = verdict.get("publication_result")
        if isinstance(publication_result, dict):
            comparisons = {
                "execution_status": (publication.get("execution_status"), verdict.get("execution_status")),
                "commit_created": (
                    publication.get("commit_created"),
                    publication_result.get("commit_created"),
                ),
                "branch_pushed": (
                    publication.get("branch_pushed"),
                    publication_result.get("branch_pushed"),
                ),
                "pr_created_or_updated": (
                    publication.get("pr_created_or_updated"),
                    publication_result.get("pr_created_or_updated"),
                ),
                "pr_url": (publication.get("pr_url"), publication_result.get("pr_url")),
                "pr_state": (publication.get("pr_state"), publication_result.get("pr_state")),
            }
            for field, (publication_value, verdict_value) in comparisons.items():
                if publication_value != verdict_value:
                    errors.append(f"publication/verdict mismatch for {field}")
    change_set = artifacts.get("change_set")
    if change_set is not None:
        target_repository = change_set.get("target_repository")
        if isinstance(target_repository, str):
            path = Path(target_repository)
            if path.is_absolute():
                errors.append("change_set.json: target_repository must be relative")
            if ".." in path.parts:
                errors.append("change_set.json: target_repository must not contain '..'")
        for field in ("include", "exclude"):
            paths = change_set.get(field)
            if isinstance(paths, list):
                for item in paths:
                    if not isinstance(item, str):
                        continue
                    path = Path(item)
                    if path.is_absolute() or ".." in path.parts:
                        errors.append(f"change_set.json: {field} contains unsafe path {item!r}")
    return errors


def validate_profile_command_selection(
    project_profile_artifact: dict[str, Any],
    profiles_doc: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    profile_name = project_profile_artifact.get("project_profile")
    profiles = profiles_doc.get("profiles", {})
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        return [f"project_profile.json: selected profile {profile_name!r} is not defined"]
    for artifact_field, profile_field in (
        ("quality_commands_selected", "quality_commands"),
        ("security_commands_selected", "security_commands"),
    ):
        selected = project_profile_artifact.get(artifact_field)
        if not isinstance(selected, list):
            continue
        commands = profile.get(profile_field)
        required = commands.get("required", []) if isinstance(commands, dict) else []
        for command in required:
            if command not in selected:
                errors.append(
                    f"project_profile.json: {artifact_field} missing required command {command!r}"
                )
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ARTIFACTS,
        help="Validate task artifacts from this directory instead of root artifacts/.",
    )
    return parser.parse_args()


def main(artifacts_dir: Path | None = None) -> int:
    artifacts_root = (artifacts_dir or parse_args().artifacts_dir).resolve()
    errors: list[str] = []
    loaded_artifacts: dict[str, dict[str, Any]] = {}
    for name, (artifact_path, schema_path) in json_artifacts(artifacts_root).items():
        data, artifact_errors = load_and_validate_json_artifact(name, artifact_path, schema_path)
        errors.extend(artifact_errors)
        if data is not None:
            loaded_artifacts[name] = data

    errors.extend(validate_audit_log(artifacts_root))
    policy_doc, policy_errors = load_yaml(POLICY, ".agent-policy.yaml")
    errors.extend(policy_errors)
    if policy_doc is not None:
        errors.extend(validate_policy_data(policy_doc))
    profiles_doc, profile_errors = load_yaml(PROJECT_PROFILES, ".agent-project-profiles.yaml")
    errors.extend(profile_errors)
    if profiles_doc is not None:
        errors.extend(validate_project_profiles_data(profiles_doc))
    workflows_doc, workflow_errors = load_yaml(AGENT_WORKFLOWS, ".agent-workflows.yaml")
    errors.extend(workflow_errors)
    if workflows_doc is not None:
        errors.extend(validate_agent_workflows_data(workflows_doc))
    repositories_doc, repository_errors = load_yaml(AGENT_REPOSITORIES, ".agent-repositories.yaml")
    errors.extend(repository_errors)
    if repositories_doc is not None:
        errors.extend(validate_registry_data(repositories_doc))

    risk = loaded_artifacts.get("risk")
    verdict = loaded_artifacts.get("verdict")
    if risk is not None:
        errors.extend(validate_risk_invariants(risk))
    if verdict is not None:
        errors.extend(validate_verdict_invariants(verdict))
    if {"risk", "quality", "verdict", "project_profile"}.issubset(loaded_artifacts):
        errors.extend(validate_cross_artifact_invariants(loaded_artifacts))
    if profiles_doc is not None and "project_profile" in loaded_artifacts:
        errors.extend(validate_profile_command_selection(loaded_artifacts["project_profile"], profiles_doc))

    if errors:
        for error in errors:
            print(error)
        return 1

    print("artifact validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
