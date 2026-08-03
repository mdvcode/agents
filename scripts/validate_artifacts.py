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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.evaluation.io import EvaluationInputError
from ai_harness.evaluation.corpus import (
    CORPUS_METRICS,
    compare_corpus_to_baseline,
    evaluate_corpus,
    validate_corpus_dataset,
)
from ai_harness.evaluation.runner import validate_dataset
from ai_harness.evaluation.scoring import validate_rubric

ARTIFACTS = ROOT / ".agent-runs" / "UNSPECIFIED" / "artifacts"
SCHEMAS = ROOT / "schemas"
EVALS = ROOT / "evals"
POLICY = ROOT / ".agent-policy.yaml"
PROJECT_PROFILES = ROOT / ".agent-project-profiles.yaml"
AGENT_WORKFLOWS = ROOT / ".agent-workflows.yaml"
AGENT_RUNTIME = ROOT / ".agent-runtime.yaml"
AGENT_ROUTING = ROOT / ".agent-routing.yaml"
AGENT_REPOSITORIES = ROOT / ".agent-repositories.yaml"
AGENT_ROLE_CONTRACTS = ROOT / ".agent-role-contracts.yaml"
AGENT_ARTIFACT_OWNERS = ROOT / ".agent-artifact-owners.yaml"
AGENT_ROLE_CAPABILITIES = ROOT / ".agent-role-capabilities.yaml"
AGENT_TOOL_POLICY = ROOT / ".agent-tool-policy.yaml"
DEPRECATED_COMBINED_PUBLICATION_KEY = "commit" + "_push"
JSON_ARTIFACTS = {
    "risk": (ARTIFACTS / "risk.json", SCHEMAS / "risk.schema.json"),
    "quality": (ARTIFACTS / "quality.json", SCHEMAS / "quality.schema.json"),
    "security": (ARTIFACTS / "security.json", SCHEMAS / "security.schema.json"),
    "review": (ARTIFACTS / "review.json", SCHEMAS / "review.schema.json"),
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
    if not artifact_path.exists():
        return None, [f"{artifact_path.name}: missing"]
    try:
        data = load_json(artifact_path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"{artifact_path.name}: invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return None, [f"{name}.json: top-level value must be an object"]
    schema = load_json(schema_path)
    return data, validate_required(data, schema, f"{name}.json")


def validate_audit_log(run_dir: Path) -> list[str]:
    errors: list[str] = []
    path = run_dir / "audit-log.jsonl"
    if not path.exists():
        return ["audit-log.jsonl: missing"]
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as exc:
                errors.append(f"audit-log.jsonl:{line_number}: invalid JSON: {exc}")
                continue
            for field in ("time", "agent", "action", "verdict", "checks_passed"):
                if field not in entry:
                    errors.append(f"audit-log.jsonl:{line_number}: missing {field!r}")
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


def validate_tool_policy_data(policy: Any, capabilities: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(policy, dict) or policy.get("version") != 1:
        return [".agent-tool-policy.yaml: version must be 1"]
    tools = policy.get("tools")
    if not isinstance(tools, dict) or not tools:
        return [".agent-tool-policy.yaml: tools must be a non-empty object"]
    for name, rule in tools.items():
        if not isinstance(rule, dict):
            errors.append(f".agent-tool-policy.yaml: tool {name!r} must be an object")
            continue
        for field in ("roles", "allowed", "side_effects", "timeout_seconds"):
            if field not in rule:
                errors.append(f".agent-tool-policy.yaml: tool {name!r} missing {field!r}")
        if not isinstance(rule.get("timeout_seconds"), int) or int(rule.get("timeout_seconds", 0)) <= 0:
            errors.append(f".agent-tool-policy.yaml: tool {name!r} timeout_seconds must be positive")
    github = tools.get("github", {})
    if isinstance(github, dict):
        forbidden = github.get("forbidden", [])
        for action in ("merge", "force_push"):
            if action not in forbidden:
                errors.append(f".agent-tool-policy.yaml: github must forbid {action}")
    playwright = tools.get("playwright", {})
    if isinstance(playwright, dict):
        domains = playwright.get("network_domains", [])
        if sorted(domains) != sorted(["localhost", "127.0.0.1", "::1"]):
            errors.append(".agent-tool-policy.yaml: playwright network scope must be loopback-only")
    shell = tools.get("shell", {})
    if isinstance(shell, dict):
        if shell.get("command_source") != "project_profile":
            errors.append(".agent-tool-policy.yaml: shell command_source must be project_profile")
        if "arbitrary_network" not in shell.get("forbidden", []):
            errors.append(".agent-tool-policy.yaml: shell must forbid arbitrary_network")
    roles = capabilities.get("roles", {}) if isinstance(capabilities, dict) else {}
    if not isinstance(roles, dict):
        return errors + [".agent-role-capabilities.yaml: roles must be an object"]
    for role, capability in roles.items():
        if not isinstance(capability, dict):
            continue
        for tool in capability.get("tools", []):
            rule = tools.get(tool)
            if not isinstance(rule, dict):
                errors.append(f".agent-role-capabilities.yaml: {role} uses undeclared tool {tool}")
            elif role not in rule.get("roles", []):
                errors.append(f".agent-tool-policy.yaml: {role} is missing from roles for {tool}")
    return errors


def validate_verifier_contracts() -> list[str]:
    common = load_json(SCHEMAS / "verifier_artifact.schema.json")
    required = set(common.get("required", [])) if isinstance(common, dict) else set()
    role_schemas = {
        "security-agent": SCHEMAS / "roles" / "security-agent.schema.json",
        "frontend-qa-agent": SCHEMAS / "roles" / "frontend-qa-agent.schema.json",
        "architecture-consistency-agent": SCHEMAS / "roles" / "architecture-consistency.schema.json",
        "semantic-conflict-agent": SCHEMAS / "roles" / "semantic-conflict.schema.json",
        "reviewer": SCHEMAS / "roles" / "reviewer.schema.json",
    }
    errors: list[str] = []
    for role, path in role_schemas.items():
        schema = load_json(path)
        artifact = schema.get("artifact", {}) if isinstance(schema, dict) else {}
        missing = required - set(artifact.get("required", [])) if isinstance(artifact, dict) else required
        if missing:
            errors.append(f"{role}: verifier artifact contract missing {sorted(missing)}")
        enums = artifact.get("enums", {}) if isinstance(artifact, dict) else {}
        if enums.get("verdict") != ["works", "broken", "unavailable"]:
            errors.append(f"{role}: verifier verdict enum is not authoritative")
    return errors


def validate_eval_contracts() -> list[str]:
    errors: list[str] = []
    schema_paths = sorted(SCHEMAS.glob("eval_*.schema.json"))
    if len(schema_paths) < 5:
        errors.append("schemas: expected evaluation dataset, rubric, report, comparison, and leaderboard schemas")
    for path in schema_paths:
        try:
            load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
    inputs = {
        "dataset": EVALS / "datasets" / "harness_completed_run_v1.json",
        "rubric": EVALS / "rubrics" / "harness_run_v1.json",
        "benchmark": EVALS / "benchmarks" / "milestone3_v1.json",
        "golden_task": EVALS / "golden_tasks" / "completed_engineering_run_v1.json",
        "regressions": EVALS / "regressions" / "harness_failure_taxonomy_v1.json",
    }
    loaded: dict[str, dict[str, Any]] = {}
    for label, path in inputs.items():
        if not path.is_file():
            errors.append(f"{path.relative_to(ROOT)}: missing")
            continue
        try:
            value = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.relative_to(ROOT)}: top-level value must be an object")
            continue
        loaded[label] = value
    try:
        if "dataset" in loaded:
            validate_dataset(loaded["dataset"])
        if "rubric" in loaded:
            validate_rubric(loaded["rubric"])
    except EvaluationInputError as exc:
        errors.append(f"evaluation contract: {exc}")
    benchmark = loaded.get("benchmark")
    if benchmark is not None:
        for field in ("schema_version", "name", "dataset", "rubric", "comparison"):
            if field not in benchmark:
                errors.append(f"evals/benchmarks/milestone3_v1.json: missing {field!r}")
    errors.extend(validate_production_corpus_contracts())
    return errors


def validate_production_corpus_contracts() -> list[str]:
    errors: list[str] = []
    dataset_paths = [
        EVALS / "datasets" / filename
        for filename in (
            "core_engineering_v1.json",
            "security_routing_v1.json",
            "context_retrieval_v1.json",
            "repair_loops_v1.json",
            "publication_safety_v1.json",
            "human_approval_v1.json",
        )
    ]
    datasets: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    negative_cases = 0
    for path in dataset_paths:
        if not path.is_file():
            errors.append(f"{path.relative_to(ROOT)}: missing")
            continue
        try:
            value = load_json(path)
            if not isinstance(value, dict):
                raise EvaluationInputError("top-level value must be an object")
            validate_corpus_dataset(value)
        except (OSError, json.JSONDecodeError, EvaluationInputError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
            continue
        datasets.append(value)
        for case in value["cases"]:
            case_id = str(case["case_id"])
            if case_id in case_ids:
                errors.append(f"production corpus duplicate case id: {case_id}")
            case_ids.add(case_id)
            if "negative" in case.get("tags", []):
                negative_cases += 1
    if len(case_ids) < 30:
        errors.append(f"production corpus requires at least 30 cases, found {len(case_ids)}")
    if negative_cases < 10:
        errors.append(f"production corpus requires at least 10 negative cases, found {negative_cases}")

    golden_path = EVALS / "golden_tasks" / "production_engineering_v1.json"
    regressions_path = EVALS / "regressions" / "production_regressions_v1.json"
    baseline_path = EVALS / "baselines" / "production_e2_v1.json"
    experiment_path = EVALS / "experiments" / "production_e2_v1.json"
    benchmark_path = EVALS / "benchmarks" / "production_e2_v1.json"
    values: dict[str, dict[str, Any]] = {}
    for label, path in {
        "golden": golden_path,
        "regressions": regressions_path,
        "baseline": baseline_path,
        "experiment": experiment_path,
        "benchmark": benchmark_path,
    }.items():
        if not path.is_file():
            errors.append(f"{path.relative_to(ROOT)}: missing")
            continue
        try:
            value = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.relative_to(ROOT)}: top-level value must be an object")
            continue
        values[label] = value
    golden = values.get("golden", {}).get("tasks")
    if not isinstance(golden, list) or len(golden) < 20:
        errors.append("production golden task corpus requires at least 20 tasks")
    else:
        golden_ids = [task.get("task_id") for task in golden if isinstance(task, dict)]
        if len(golden_ids) != len(golden) or any(
            not isinstance(task_id, str) or not task_id for task_id in golden_ids
        ):
            errors.append("production golden tasks require non-empty task_id values")
        elif len(set(golden_ids)) != len(golden_ids):
            errors.append("production golden task ids must be unique")
    regressions = values.get("regressions", {}).get("regressions")
    if not isinstance(regressions, list) or len(regressions) < 10:
        errors.append("production regression corpus requires at least 10 cases")
    else:
        regression_ids: set[str] = set()
        for index, regression in enumerate(regressions):
            if not isinstance(regression, dict):
                errors.append(f"production regression {index} must be an object")
                continue
            regression_id = regression.get("regression_id")
            source_case_id = regression.get("source_case_id")
            metric = regression.get("metric")
            mutation = regression.get("mutation")
            if not isinstance(regression_id, str) or not regression_id:
                errors.append(f"production regression {index} requires regression_id")
            elif regression_id in regression_ids:
                errors.append(f"production regression id is duplicated: {regression_id}")
            else:
                regression_ids.add(regression_id)
            if source_case_id not in case_ids:
                errors.append(
                    f"production regression {regression_id!r} references unknown case "
                    f"{source_case_id!r}"
                )
            if metric not in CORPUS_METRICS:
                errors.append(
                    f"production regression {regression_id!r} uses unknown metric {metric!r}"
                )
            if not isinstance(mutation, dict) or not isinstance(mutation.get("field"), str):
                errors.append(
                    f"production regression {regression_id!r} requires a declarative mutation"
                )
    experiment = values.get("experiment")
    baseline = values.get("baseline")
    if experiment is not None:
        for field in ("schema_version", "kind", "experiment_id", "baseline", "candidate", "datasets", "thresholds"):
            if field not in experiment:
                errors.append(f"evals/experiments/production_e2_v1.json: missing {field!r}")
    benchmark = values.get("benchmark")
    if benchmark is not None:
        for field in (
            "schema_version",
            "name",
            "datasets",
            "golden_tasks",
            "regressions",
            "baseline",
            "experiment",
            "thresholds",
        ):
            if field not in benchmark:
                errors.append(f"evals/benchmarks/production_e2_v1.json: missing {field!r}")
        if experiment is not None and benchmark.get("datasets") != experiment.get("datasets"):
            errors.append("production benchmark and experiment datasets must match")
        if experiment is not None and benchmark.get("thresholds") != experiment.get("thresholds"):
            errors.append("production benchmark and experiment thresholds must match")
    if datasets and baseline is not None and experiment is not None:
        try:
            candidate = evaluate_corpus(datasets)
            gate = compare_corpus_to_baseline(baseline, candidate, experiment["thresholds"])
            if gate["status"] != "pass":
                errors.extend(f"production corpus baseline: {item}" for item in gate["blockers"])
        except (EvaluationInputError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"production corpus baseline: {exc}")
    return errors


def validate_observability_contracts() -> list[str]:
    errors: list[str] = []
    expected = {
        "otel_span.schema.json": {"schema_version", "name", "trace_id", "span_id", "status", "attributes"},
        "observability_snapshot.schema.json": {
            "schema_version",
            "generated_at",
            "overview",
            "runs",
            "workers",
            "queue",
            "latency",
            "costs",
            "retries",
            "loops",
            "failures",
            "tracing",
        },
    }
    for filename, required_fields in expected.items():
        path = SCHEMAS / filename
        if not path.exists():
            errors.append(f"schemas: missing {filename}")
            continue
        try:
            schema = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"schemas/{filename}: invalid JSON: {exc}")
            continue
        if schema.get("type") != "object":
            errors.append(f"schemas/{filename}: top-level type must be object")
        required = schema.get("required")
        if not isinstance(required, list) or not required_fields.issubset(required):
            errors.append(f"schemas/{filename}: missing required observability fields")
    return errors


def validate_role_execution_contracts(contracts_doc: Any) -> list[str]:
    if not isinstance(contracts_doc, dict):
        return [".agent-role-contracts.yaml: top-level value must be an object"]
    roles = contracts_doc.get("roles", {})
    if not isinstance(roles, dict):
        return [".agent-role-contracts.yaml: roles must be an object"]
    issue_intake = roles.get("issue-intake", {})
    if not isinstance(issue_intake, dict):
        return [".agent-role-contracts.yaml: issue-intake must be an object"]
    errors: list[str] = []
    if issue_intake.get("execution_kind") != "harness_stage":
        errors.append(".agent-role-contracts.yaml: issue-intake must be a harness_stage")
    if issue_intake.get("llm_invocation") is not False:
        errors.append(".agent-role-contracts.yaml: issue-intake must set llm_invocation=false")
    if issue_intake.get("prompt_path") not in {"", None}:
        errors.append(".agent-role-contracts.yaml: issue-intake harness stage must not have an LLM prompt")
    return errors


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
    web = profiles.get("nextjs_web", {})
    frontend = web.get("frontend_evidence", {}) if isinstance(web, dict) else {}
    if not isinstance(frontend, dict):
        errors.append(f"{label}: profiles.nextjs_web.frontend_evidence must be an object")
    else:
        if not isinstance(frontend.get("dev_command"), str) or not frontend.get("dev_command"):
            errors.append(f"{label}: nextjs_web frontend dev_command is required")
        if str(frontend.get("local_url", "")) != "http://127.0.0.1:3000":
            errors.append(f"{label}: nextjs_web frontend local_url must use loopback")
        if sorted(frontend.get("network_scope", [])) != sorted(["localhost", "127.0.0.1", "::1"]):
            errors.append(f"{label}: nextjs_web frontend network_scope must be loopback-only")
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
    publish_executor = publish_pr.get("executor")
    if not isinstance(publish_executor, str) or "--run-id {run_id}" not in publish_executor or "--artifacts-dir {artifacts_dir}" not in publish_executor:
        errors.append(f"{label}: workflows.publish_pr.executor must use the authoritative run id and artifacts directory")
    full = workflows.get("full_agent_workflow")
    if not isinstance(full, dict):
        errors.append(f"{label}: workflows.full_agent_workflow must be an object")
    else:
        if full.get("runtime_provider") != "codex-cli":
            errors.append(f"{label}: workflows.full_agent_workflow.runtime_provider must be codex-cli")
        if "adapter_command" in full:
            errors.append(f"{label}: workflows.full_agent_workflow must not configure a provider-specific adapter command")
        budgets = full.get("budgets")
        if not isinstance(budgets, dict):
            errors.append(f"{label}: workflows.full_agent_workflow.budgets must be an object")
        else:
            for key in ("max_roles", "max_repair_iterations", "max_duration_seconds", "max_tokens"):
                if not isinstance(budgets.get(key), int) or budgets[key] <= 0:
                    errors.append(f"{label}: workflows.full_agent_workflow.budgets.{key} must be a positive integer")
        executor = full.get("executor")
        if not isinstance(executor, str) or "--run-id {run_id}" not in executor:
            errors.append(f"{label}: workflows.full_agent_workflow.executor must pass the shared run id")
        if not isinstance(executor, str) or "--artifacts-dir {artifacts_dir}" not in executor:
            errors.append(f"{label}: workflows.full_agent_workflow.executor must pass the run-scoped artifacts dir")
        if not isinstance(executor, str) or "--create-worktree" not in executor:
            errors.append(f"{label}: workflows.full_agent_workflow.executor must create a task worktree")
        if not isinstance(executor, str) or "--runtime-provider {runtime_provider}" not in executor:
            errors.append(f"{label}: workflows.full_agent_workflow.executor must use the runtime provider boundary")
    mutation_rules = publish_pr.get("mutation_rules")
    if not isinstance(mutation_rules, list) or not any("git add -A" in rule for rule in mutation_rules):
        errors.append(f"{label}: workflows.publish_pr.mutation_rules must forbid git add -A")
    return errors


def validate_runtime_config_data(data: Any, label: str = ".agent-runtime.yaml") -> list[str]:
    if not isinstance(data, dict) or data.get("version") != 1:
        return [f"{label}: version must be 1"]
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        return [f"{label}: runtime must be an object"]
    schema = load_json(SCHEMAS / "runtime_config.schema.json")
    errors = validate_required(runtime, schema, f"{label}.runtime")
    if runtime.get("api_required") is not False:
        errors.append(f"{label}: Step 2 codex-cli runtime must not require an API")
    if runtime.get("model_router") is not False:
        errors.append(f"{label}: model_router is forbidden before Step 4")
    if "codex_cli_executor.py" not in str(runtime.get("executor_command", "")):
        errors.append(f"{label}: codex-cli executor command must use the Codex CLI adapter")
    return errors


def validate_agent_routing_data(
    routing_doc: Any, label: str = ".agent-routing.yaml"
) -> list[str]:
    errors: list[str] = []
    if not isinstance(routing_doc, dict):
        return [f"{label}: top-level value must be an object"]
    if routing_doc.get("version") != 1:
        errors.append(f"{label}: version must be 1")
    required = routing_doc.get("required_before_publication")
    expected_required = [
        "issue-intake",
        "context-compiler",
        "planner",
        "risk-classifier",
        "implementation-agent",
        "test-generator",
        "quality-runner",
        "security-agent",
        "reviewer",
        "orchestrator",
        "publication-prepare",
    ]
    if required != expected_required:
        errors.append(f"{label}: required_before_publication must be {expected_required!r}")
    optional = routing_doc.get("optional_gates")
    if not isinstance(optional, dict):
        errors.append(f"{label}: optional_gates must be an object")
    else:
        for gate in ("frontend_qa", "architecture_consistency", "semantic_conflict"):
            value = optional.get(gate)
            if not isinstance(value, dict) or not isinstance(value.get("role"), str):
                errors.append(f"{label}: optional_gates.{gate} must define a role")
    routing = routing_doc.get("routing")
    if not isinstance(routing, dict):
        errors.append(f"{label}: routing must be an object")
    else:
        for name in (
            "high_risk",
            "security_critical",
            "security_review_required",
            "quality_failed",
            "review_blocked",
            "ci_failed",
            "frontend_verification_failed",
        ):
            if not isinstance(routing.get(name), dict):
                errors.append(f"{label}: routing.{name} must be an object")
        critical = routing.get("security_critical", {})
        if not isinstance(critical, dict) or critical.get("next") != "blocked":
            errors.append(f"{label}: routing.security_critical.next must be blocked")
        review_required = routing.get("security_review_required", {})
        if not isinstance(review_required, dict) or review_required.get("next") != "approval-gate":
            errors.append(f"{label}: routing.security_review_required.next must be approval-gate")
        for name in ("quality_failed", "review_blocked", "ci_failed", "frontend_verification_failed"):
            entry = routing.get(name, {})
            loop = entry.get("loop", {}) if isinstance(entry, dict) else {}
            for field in ("max_iterations", "max_tokens", "max_duration_seconds"):
                if not isinstance(loop.get(field), int) or int(loop.get(field, 0)) <= 0:
                    errors.append(f"{label}: routing.{name}.loop.{field} must be positive")
    schema_path = ROOT / "schemas" / "workflow_route.schema.json"
    workflow_state_schema = ROOT / "schemas" / "agent_workflow.schema.json"
    for path in (schema_path, workflow_state_schema):
        if not path.exists():
            errors.append(f"{path.relative_to(ROOT)}: missing")
        else:
            try:
                load_json(path)
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
    return errors


def validate_artifact_ownership_data(owners_doc: Any, contracts_doc: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(owners_doc, dict) or owners_doc.get("version") != 1:
        return [".agent-artifact-owners.yaml: invalid top-level contract"]
    artifacts = owners_doc.get("artifacts")
    patterns = owners_doc.get("patterns", {})
    roles = contracts_doc.get("roles") if isinstance(contracts_doc, dict) else None
    if not isinstance(artifacts, dict) or not isinstance(roles, dict):
        return ["artifact ownership and role contracts must define objects"]
    declared: dict[str, str] = {}
    for role, contract in roles.items():
        if not isinstance(contract, dict):
            continue
        for artifact in contract.get("expected_artifacts", []):
            if not isinstance(artifact, str):
                errors.append(f"role {role}: expected artifact must be a string")
                continue
            previous = declared.get(artifact)
            if previous is not None and previous != role:
                errors.append(f"artifact {artifact} has multiple owners: {previous}, {role}")
            declared[artifact] = str(role)
    for artifact, owner in artifacts.items():
        if declared.get(str(artifact)) != owner:
            errors.append(
                f"artifact owner mismatch for {artifact}: registry={owner!r}, contract={declared.get(str(artifact))!r}"
            )
    for artifact, owner in declared.items():
        if artifacts.get(artifact) != owner:
            errors.append(f"role contract artifact missing from ownership registry: {artifact}")
    if not isinstance(patterns, dict):
        errors.append(".agent-artifact-owners.yaml: patterns must be an object")
        patterns = {}
    declared_patterns: dict[str, str] = {}
    for role, contract in roles.items():
        if not isinstance(contract, dict):
            continue
        for pattern in contract.get("owned_artifact_patterns", []):
            if not isinstance(pattern, str) or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                errors.append(f"role {role}: unsafe owned artifact pattern {pattern!r}")
                continue
            declared_patterns[pattern] = str(role)
    if patterns != declared_patterns:
        errors.append(
            f"artifact pattern ownership mismatch: registry={patterns!r}, contract={declared_patterns!r}"
        )
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
    checks_passed = verdict.get("checks_passed")
    approval_before_publish = verdict.get("approval_required_before_publish")
    visual_evidence = verdict.get("visual_evidence")
    high_risk_triggers = verdict.get("high_risk_triggers")
    protected_paths_touched = verdict.get("protected_paths_touched")

    evidence_required = isinstance(visual_evidence, dict) and visual_evidence.get("required") is True
    evidence_provided = isinstance(visual_evidence, dict) and visual_evidence.get("provided") is True

    if risk_class == "high" and decision not in {"await_approval", "reject"}:
        errors.append(f"{label}: high risk must use await_approval or reject")
    if high_risk_triggers:
        if decision not in {"await_approval", "reject"}:
            errors.append(f"{label}: high_risk_triggers require await_approval or reject")
    if decision == "publish_pr" and risk_class == "high":
        errors.append(f"{label}: publish_pr is not allowed for high risk")
    if decision == "await_approval" and approval_before_publish is not True:
        errors.append(f"{label}: await_approval requires approval_required_before_publish=true")
    if decision == "publish_pr" and execution_status not in {"planned", "running"}:
        errors.append(f"{label}: publish_pr verdict must remain a pre-publication decision")
    if decision == "publish_pr" and checks_passed is not True:
        errors.append(f"{label}: publish_pr requires checks_passed=true")
    if decision == "publish_pr" and evidence_required and not evidence_provided:
        errors.append(f"{label}: missing required visual evidence requires draft publication handling")
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
        if verdict.get("decision") != "publish_pr":
            errors.append("publication exists without a publish_pr orchestrator verdict")
        if risk_class == "high" and any(
            publication.get(field) is True
            for field in ("commit_created", "branch_pushed", "pr_created_or_updated")
        ):
            errors.append("high risk publication must not create commits, push branches, or publish PRs")
        if publication.get("execution_status") == "completed":
            if publication.get("pr_created_or_updated") is not True or not publication.get("pr_url"):
                errors.append("completed publication requires a PR URL")
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
        default=None,
        help="Validate task artifacts from .agent-runs/<run-id>/artifacts.",
    )
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument(
        "--phase",
        choices=("pre-publication", "complete"),
        default="complete",
    )
    parser.add_argument("--contracts-only", action="store_true")
    return parser.parse_args()


def main(artifacts_dir: Path | None = None) -> int:
    args = parse_args() if artifacts_dir is None else argparse.Namespace(
        artifacts_dir=artifacts_dir,
        run_dir=None,
        phase="complete",
        contracts_only=False,
    )
    selected_artifacts = args.artifacts_dir or (args.run_dir / "artifacts" if args.run_dir else None)
    artifacts_root = selected_artifacts.resolve() if selected_artifacts else None
    errors: list[str] = []
    loaded_artifacts: dict[str, dict[str, Any]] = {}
    if not args.contracts_only:
        if artifacts_root is None:
            errors.append("--run-dir or --artifacts-dir is required; root artifacts/ is forbidden")
        else:
            required_names = {
                "risk",
                "quality",
                "security",
                "review",
                "verdict",
                "project_profile",
                "change_set",
                "publication_payload",
            }
            if args.phase == "complete":
                required_names.add("publication")
            for name, (artifact_path, schema_path) in json_artifacts(artifacts_root).items():
                if name not in required_names:
                    continue
                data, artifact_errors = load_and_validate_json_artifact(name, artifact_path, schema_path)
                errors.extend(artifact_errors)
                if data is not None:
                    loaded_artifacts[name] = data
            if args.phase == "complete":
                errors.extend(validate_audit_log(artifacts_root.parent))
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
    runtime_doc, runtime_errors = load_yaml(AGENT_RUNTIME, ".agent-runtime.yaml")
    errors.extend(runtime_errors)
    if runtime_doc is not None:
        errors.extend(validate_runtime_config_data(runtime_doc))
    routing_doc, routing_errors = load_yaml(AGENT_ROUTING, ".agent-routing.yaml")
    errors.extend(routing_errors)
    if routing_doc is not None:
        errors.extend(validate_agent_routing_data(routing_doc))
    repositories_doc, repository_errors = load_yaml(AGENT_REPOSITORIES, ".agent-repositories.yaml")
    errors.extend(repository_errors)
    if repositories_doc is not None:
        errors.extend(validate_registry_data(repositories_doc))
    contracts_doc, contract_errors = load_yaml(AGENT_ROLE_CONTRACTS, ".agent-role-contracts.yaml")
    owners_doc, owner_errors = load_yaml(AGENT_ARTIFACT_OWNERS, ".agent-artifact-owners.yaml")
    errors.extend(contract_errors + owner_errors)
    if contracts_doc is not None and owners_doc is not None:
        errors.extend(validate_artifact_ownership_data(owners_doc, contracts_doc))
        errors.extend(validate_role_execution_contracts(contracts_doc))
    tool_policy_doc, tool_policy_errors = load_yaml(AGENT_TOOL_POLICY, ".agent-tool-policy.yaml")
    capabilities_doc, capability_errors = load_yaml(
        AGENT_ROLE_CAPABILITIES,
        ".agent-role-capabilities.yaml",
    )
    errors.extend(tool_policy_errors + capability_errors)
    if tool_policy_doc is not None and capabilities_doc is not None:
        errors.extend(validate_tool_policy_data(tool_policy_doc, capabilities_doc))
    errors.extend(validate_verifier_contracts())
    errors.extend(validate_eval_contracts())
    errors.extend(validate_observability_contracts())

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
