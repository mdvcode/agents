"""Bounded, read-only Adaptive Acceptance projections for the control plane."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACCEPTANCE_PATH = ROOT / "evals" / "adaptive_execution_acceptance.json"
_COMPARISON_KEYS = {
    "key",
    "label",
    "full",
    "adaptive",
    "delta",
    "format",
    "delta_format",
    "required_adaptive_value",
}
_PAIR_FIELDS = {
    "case_id",
    "task_class",
    "scope",
    "risk",
    "repository",
    "mode",
    "model",
    "role",
    "outcome",
    "success",
    "model_calls",
    "input_tokens",
    "uncached_input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "context_cache_hit_rate",
    "roles_executed",
    "roles_skipped",
    "model_escalations",
    "duration_seconds",
    "pr_success",
    "repair_count",
    "human_interventions",
    "mandatory_security_gates_missed",
    "high_risk_approval_bypasses",
}


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _fingerprint(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError:
        return ""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _bounded_text(value: Any, limit: int = 200) -> str:
    return str(value)[:limit]


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _comparison(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value[:30]:
        if not isinstance(raw, dict):
            continue
        row = {key: raw[key] for key in _COMPARISON_KEYS if key in raw}
        row["key"] = _bounded_text(row.get("key", ""), 80)
        row["label"] = _bounded_text(row.get("label", ""), 120)
        row["format"] = _bounded_text(row.get("format", ""), 40)
        row["delta_format"] = _bounded_text(row.get("delta_format", ""), 40)
        for field in ("full", "adaptive", "delta", "required_adaptive_value"):
            if field in row and row[field] is not None and not isinstance(row[field], (int, float)):
                row[field] = None
        if row["key"] and row["label"]:
            rows.append(row)
    return rows


def _pair_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    row = {key: value[key] for key in _PAIR_FIELDS if key in value}
    for key in ("case_id", "task_class", "scope", "risk", "repository", "mode", "outcome"):
        if key in row:
            row[key] = _bounded_text(row[key])
    for key in ("model", "role"):
        raw = row.get(key, [])
        row[key] = [_bounded_text(item, 120) for item in raw[:30]] if isinstance(raw, list) else []
    for key in _PAIR_FIELDS - {
        "case_id", "task_class", "scope", "risk", "repository", "mode", "model", "role", "outcome"
    }:
        if key in row and key != "success":
            row[key] = _number(row[key])
    if "success" in row:
        row["success"] = row["success"] is True
    return row


def _acceptance_summary(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value[:20]:
        if not isinstance(raw, dict):
            continue
        rows.append(
            {
                "key": _bounded_text(raw.get("key", ""), 80),
                "label": _bounded_text(raw.get("label", ""), 120),
                "value": _number(raw.get("value")),
                "unit": _bounded_text(raw.get("unit", ""), 40),
                "status": _bounded_text(raw.get("status", ""), 20),
            }
        )
    return rows


def _breakdowns(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for dimension in ("task_class", "scope", "risk", "repository", "model", "role", "outcome"):
        raw_segments = value.get(dimension, [])
        if not isinstance(raw_segments, list):
            continue
        segments: list[dict[str, Any]] = []
        for raw in raw_segments[:100]:
            if not isinstance(raw, dict):
                continue
            segments.append(
                {
                    "value": _bounded_text(raw.get("value", ""), 160),
                    "paired_tasks": _integer(raw.get("paired_tasks")),
                    "comparison": _comparison(raw.get("comparison")),
                }
            )
        result[dimension] = segments
    return result


def load_adaptive_acceptance(
    *,
    runs_dir: Path,
    acceptance_path: Path = DEFAULT_ACCEPTANCE_PATH,
) -> dict[str, Any]:
    """Read a fingerprint-matched evaluator report without re-evaluating acceptance."""

    decision = _read_object(acceptance_path)
    base = {
        "display_status": "NOT ENOUGH DATA",
        "overall": "NOT ENOUGH DATA",
        "adaptive_default_allowed": False,
        "dataset_cases": _integer(decision.get("dataset_cases")),
        "evidence_kind": _bounded_text(decision.get("evidence_kind", ""), 80),
        "acceptance_summary": [],
        "comparison": [],
        "breakdowns": {},
        "pairs": [],
        "blockers": [
            _bounded_text(item, 500)
            for item in decision.get("blockers", [])[:20]
            if isinstance(item, str)
        ] if isinstance(decision.get("blockers"), list) else [],
        "evidence_status": "authoritative acceptance has not been evaluated",
    }
    if decision.get("schema_version") != 1:
        base["evidence_status"] = "acceptance decision is missing or malformed"
        return base
    if decision.get("status") == "fail":
        base["display_status"] = "FAIL"
        base["overall"] = "NOT READY"
        base["evidence_status"] = "authoritative acceptance decision failed"
    raw_report_path = decision.get("report_path")
    if not isinstance(raw_report_path, str) or not raw_report_path:
        return base
    report_path = Path(raw_report_path)
    if not _under(report_path, runs_dir):
        base["evidence_status"] = "acceptance report is not run-scoped"
        return base
    expected_fingerprint = str(decision.get("report_fingerprint", ""))
    if not expected_fingerprint or _fingerprint(report_path) != expected_fingerprint:
        base["evidence_status"] = "acceptance report fingerprint does not match"
        return base
    report = _read_object(report_path)
    if (
        report.get("schema_version") != 1
        or report.get("kind") != "adaptive_ab_acceptance"
        or report.get("evidence_kind") != "paired_authoritative_runs"
        or report.get("status") != decision.get("status")
    ):
        base["evidence_status"] = "acceptance report contract does not match the decision"
        return base
    decision_status = str(decision.get("status", ""))
    if decision_status not in {"pass", "fail"}:
        return base
    allowed = decision_status == "pass" and decision.get("adaptive_default_allowed") is True
    pairs: list[dict[str, Any]] = []
    raw_pairs = report.get("pairs", [])
    if isinstance(raw_pairs, list):
        for raw_pair in raw_pairs[:100]:
            if not isinstance(raw_pair, dict):
                continue
            pairs.append(
                {
                    "case_id": _bounded_text(raw_pair.get("case_id", ""), 120),
                    "full": _pair_row(raw_pair.get("full")),
                    "adaptive": _pair_row(raw_pair.get("adaptive")),
                }
            )
    return {
        **base,
        "display_status": decision_status.upper(),
        "overall": "READY FOR DEFAULT" if allowed else "NOT READY",
        "adaptive_default_allowed": allowed,
        "dataset_cases": _integer(report.get("paired_tasks", decision.get("dataset_cases", 0))),
        "evidence_kind": "paired_authoritative_runs",
        "acceptance_summary": _acceptance_summary(report.get("acceptance_summary")),
        "comparison": _comparison(report.get("comparison")),
        "breakdowns": _breakdowns(report.get("breakdowns")),
        "pairs": pairs,
        "evidence_status": "fingerprint-verified authoritative report",
    }


def adaptive_run_detail(
    run_dir: Path,
    workflow: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Project bounded plan and efficiency evidence for one run."""

    plan = _read_object(run_dir / "execution-plan.json")
    analysis = plan.get("analysis", workflow.get("task_analysis", {}))
    if not isinstance(analysis, dict):
        analysis = {}
    checkpoints = {
        str(item.get("role", "")): item
        for item in workflow.get("roles", [])
        if isinstance(item, dict) and str(item.get("role", ""))
    } if isinstance(workflow.get("roles"), list) else {}
    model_profiles = plan.get("model_profiles", {})
    if not isinstance(model_profiles, dict):
        model_profiles = {}
    reasoning = plan.get("reasoning", {})
    if not isinstance(reasoning, dict):
        reasoning = {}
    nodes: list[dict[str, Any]] = []
    for raw_node in plan.get("nodes", []) if isinstance(plan.get("nodes"), list) else []:
        if not isinstance(raw_node, dict):
            continue
        role = str(raw_node.get("role", ""))
        checkpoint = checkpoints.get(role, {})
        result = checkpoint.get("result", {}) if isinstance(checkpoint, dict) else {}
        if not isinstance(result, dict):
            result = {}
        profile = checkpoint.get("execution_profile", {}) if isinstance(checkpoint, dict) else {}
        if not isinstance(profile, dict):
            profile = {}
        raw_status = str(result.get("status", ""))
        state = raw_status or ("running" if workflow.get("current_role") == role else "pending")
        execution_kind = str(raw_node.get("execution_kind", ""))
        nodes.append(
            {
                "role": _bounded_text(role, 120),
                "state": _bounded_text(state, 40),
                "execution_kind": _bounded_text(execution_kind, 40),
                "deterministic": execution_kind != "llm_role",
                "mandatory": raw_node.get("mandatory") is True,
                "model_profile": _bounded_text(
                    result.get(
                        "execution_profile",
                        profile.get("execution_profile", raw_node.get("model_profile", model_profiles.get(role, ""))),
                    ),
                    80,
                ),
                "model": _bounded_text(result.get("model", profile.get("model", "")), 120),
                "reason": _bounded_text(raw_node.get("reason", ""), 500),
                "checks": [
                    _bounded_text(item, 80)
                    for item in raw_node.get("deterministic_checks", [])[:20]
                    if isinstance(item, str)
                ] if isinstance(raw_node.get("deterministic_checks"), list) else [],
            }
        )
    present_roles = {node["role"] for node in nodes}
    for role in plan.get("skipped_roles", []) if isinstance(plan.get("skipped_roles"), list) else []:
        if not isinstance(role, str) or role in present_roles:
            continue
        nodes.append(
            {
                "role": _bounded_text(role, 120),
                "state": "skipped",
                "execution_kind": "skipped",
                "deterministic": False,
                "mandatory": False,
                "model_profile": _bounded_text(model_profiles.get(role, ""), 80),
                "model": "",
                "reason": _bounded_text(reasoning.get(role, ""), 500),
                "checks": [],
            }
        )
    input_tokens = _integer(metrics.get("input_tokens_per_task"))
    uncached_tokens = _integer(metrics.get("uncached_input_tokens_per_task"))
    role_metrics = [item for item in metrics.get("roles", []) if isinstance(item, dict)]
    skipped_roles = plan.get("skipped_roles", [])
    if not isinstance(skipped_roles, list):
        skipped_roles = []
    return {
        "mode": _bounded_text(workflow.get("effective_mode", workflow.get("mode", "")), 60),
        "task_class": _bounded_text(analysis.get("task_class", ""), 60),
        "scope": _bounded_text(analysis.get("scope", ""), 60),
        "risk": _bounded_text(analysis.get("risk", workflow.get("risk_class", "")), 60),
        "execution_plan": nodes,
        "efficiency": {
            "model_calls": _integer(metrics.get("model_calls_per_task")),
            "input_tokens": input_tokens,
            "uncached_input_tokens": uncached_tokens,
            "cached_input_tokens": max(0, input_tokens - uncached_tokens),
            "output_tokens": _integer(metrics.get("output_tokens_per_task")),
            "cache_hit_rate": _number(metrics.get("context_cache_hit_rate")),
            "execution_time_seconds": _number(
                metrics.get("time_to_success", workflow.get("elapsed_seconds", 0))
            ),
            "roles_executed": _integer(
                metrics.get("roles_executed_per_task", len(checkpoints)),
                len(checkpoints),
            ),
            "roles_skipped": _integer(
                metrics.get("roles_skipped_per_task", len(skipped_roles)),
                len(skipped_roles),
            ),
            "model_escalations": _integer(metrics.get("model_escalations_per_task")),
            "repair_loops": _integer(metrics.get("repair_attempts_per_task")),
        },
        "models": sorted(
            {
                _bounded_text(item.get("model", item.get("execution_profile", "")), 120)
                for item in role_metrics
                if str(item.get("model", item.get("execution_profile", "")))
            }
        ),
        "roles": sorted(checkpoints),
    }
