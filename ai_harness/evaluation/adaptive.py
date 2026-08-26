"""Golden-plan evaluation and A/B acceptance for adaptive execution."""

from __future__ import annotations

from statistics import median
import json
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_harness.planning import TaskAnalyzer, WorkflowCompiler

from .io import EvaluationInputError, utc_now


EXPECTED_DISTRIBUTION = {
    "trivial_docs": 10,
    "small_bugfix": 10,
    "tests_refactor": 10,
    "medium_feature": 10,
    "security_sensitive": 5,
    "architecture_high_risk": 5,
}


def validate_adaptive_dataset(dataset: Mapping[str, Any]) -> None:
    if dataset.get("schema_version") != 1:
        raise EvaluationInputError("adaptive dataset schema_version must be 1")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or len(cases) < 50:
        raise EvaluationInputError("adaptive dataset must contain at least 50 cases")
    counts = {category: 0 for category in EXPECTED_DISTRIBUTION}
    seen: set[str] = set()
    required = {
        "id",
        "category",
        "task",
        "repository_profile",
        "requested_paths",
        "required_roles",
        "forbidden_skips",
        "acceptable_optional_roles",
        "expected_risk",
        "expected_scope",
    }
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not required <= set(case):
            raise EvaluationInputError(f"adaptive dataset case {index} is incomplete")
        case_id = str(case["id"])
        if not case_id or case_id in seen:
            raise EvaluationInputError(f"adaptive dataset has duplicate/empty id: {case_id!r}")
        seen.add(case_id)
        category = str(case["category"])
        if category not in counts:
            raise EvaluationInputError(f"adaptive dataset case {case_id!r} has unknown category")
        counts[category] += 1
        for field in ("requested_paths", "required_roles", "forbidden_skips", "acceptable_optional_roles"):
            if not isinstance(case[field], list) or any(not isinstance(item, str) for item in case[field]):
                raise EvaluationInputError(f"adaptive dataset case {case_id!r}.{field} must be strings")
        if case["expected_risk"] not in {"low", "medium", "high"}:
            raise EvaluationInputError(f"adaptive dataset case {case_id!r} has invalid risk")
        if case["expected_scope"] not in {"trivial", "small", "medium", "large"}:
            raise EvaluationInputError(f"adaptive dataset case {case_id!r} has invalid scope")
    for category, minimum in EXPECTED_DISTRIBUTION.items():
        if counts[category] < minimum:
            raise EvaluationInputError(
                f"adaptive dataset category {category!r} needs {minimum} cases; found {counts[category]}"
            )


def evaluate_adaptive_plans(
    dataset: Mapping[str, Any],
    *,
    analyzer: TaskAnalyzer,
    compiler: WorkflowCompiler,
) -> dict[str, Any]:
    validate_adaptive_dataset(dataset)
    results: list[dict[str, Any]] = []
    blockers: list[str] = []
    for case in dataset["cases"]:
        analysis = analyzer.analyze(
            str(case["task"]),
            repository_profile=str(case["repository_profile"]),
            project_type=str(case["repository_profile"]),
            requested_paths=list(case["requested_paths"]),
        )
        plan = compiler.compile(
            analysis,
            task_id=str(case["id"]),
            mode="adaptive",
            project_profile=str(case["repository_profile"]),
        )
        failures: list[str] = []
        required_roles = set(case["required_roles"])
        missing_roles = sorted(required_roles - set(plan.required_roles))
        if missing_roles:
            failures.append("missing required roles: " + ", ".join(missing_roles))
        forbidden_skips = set(case["forbidden_skips"])
        skipped_forbidden = sorted(forbidden_skips & set(plan.skipped_roles))
        if skipped_forbidden:
            failures.append("forbidden roles skipped: " + ", ".join(skipped_forbidden))
        unexpected_optional = sorted(
            set(plan.required_roles)
            - required_roles
            - set(case["acceptable_optional_roles"])
            - set(compiler.policy.hard_gate_roles)
        )
        if unexpected_optional:
            failures.append("unexpected optional roles: " + ", ".join(unexpected_optional))
        if analysis.risk != case["expected_risk"]:
            failures.append(f"risk {analysis.risk!r} != {case['expected_risk']!r}")
        if analysis.scope != case["expected_scope"]:
            failures.append(f"scope {analysis.scope!r} != {case['expected_scope']!r}")
        status = "pass" if not failures else "fail"
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "status": status,
                "failures": failures,
                "analysis": analysis.as_dict(),
                "required_roles": list(plan.required_roles),
                "skipped_roles": list(plan.skipped_roles),
                "estimated_max_model_calls": plan.estimated_max_model_calls,
            }
        )
        blockers.extend(f"{case['id']}: {failure}" for failure in failures)
    return {
        "schema_version": 1,
        "kind": "adaptive_plan_evaluation",
        "created_at": utc_now(),
        "status": "pass" if not blockers else "fail",
        "case_count": len(results),
        "passed_cases": sum(result["status"] == "pass" for result in results),
        "cases": results,
        "blockers": blockers,
    }


def _mean(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    values = [float(row.get(field, 0) or 0) for row in rows]
    return sum(values) / len(values) if values else 0.0


def _success_rate(rows: Sequence[Mapping[str, Any]]) -> float:
    return sum(bool(row.get("success")) for row in rows) / len(rows) if rows else 0.0


def _median(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    values = [float(row.get(field, 0) or 0) for row in rows]
    return float(median(values)) if values else 0.0


def _reduction(before: float, after: float) -> float:
    return (before - after) / before if before > 0 else 0.0


def _percent_change(before: float, after: float) -> float | None:
    return (after - before) / before if before > 0 else None


def _comparison_rows(
    baseline: Sequence[Mapping[str, Any]],
    adaptive: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build display aggregates in the evaluator, not in dashboard JavaScript."""

    rate_metrics = (
        ("task_success_rate", "Task Success Rate", _success_rate(baseline), _success_rate(adaptive)),
        ("pr_success_rate", "PR Success Rate", _mean(baseline, "pr_success"), _mean(adaptive, "pr_success")),
        (
            "context_cache_hit_rate",
            "Context Cache Hit Rate",
            _mean(baseline, "context_cache_hit_rate"),
            _mean(adaptive, "context_cache_hit_rate"),
        ),
    )
    rows = [
        {
            "key": key,
            "label": label,
            "full": round(before, 6),
            "adaptive": round(after, 6),
            "delta": round((after - before) * 100, 3),
            "format": "rate",
            "delta_format": "percentage_points",
        }
        for key, label, before, after in rate_metrics
    ]
    mean_metrics = (
        ("model_calls_per_task", "Model Calls / Task", "model_calls"),
        (
            "uncached_input_tokens_per_task",
            "Uncached Input Tokens / Task",
            "uncached_input_tokens",
        ),
        ("output_tokens_per_task", "Output Tokens / Task", "output_tokens"),
        ("roles_executed_per_task", "Roles Executed / Task", "roles_executed"),
        ("roles_skipped_per_task", "Roles Skipped / Task", "roles_skipped"),
        ("repair_attempts_per_task", "Repair Attempts / Task", "repair_count"),
        ("model_escalations_per_task", "Model Escalations", "model_escalations"),
        ("human_interventions_per_task", "Human Interventions", "human_interventions"),
    )
    for key, label, field in mean_metrics:
        before = _mean(baseline, field)
        after = _mean(adaptive, field)
        delta = _percent_change(before, after)
        rows.append(
            {
                "key": key,
                "label": label,
                "full": round(before, 3),
                "adaptive": round(after, 3),
                "delta": round(delta * 100, 3) if delta is not None else None,
                "format": "number",
                "delta_format": "percent_change",
            }
        )
    before_duration = _median(baseline, "duration_seconds")
    after_duration = _median(adaptive, "duration_seconds")
    duration_delta = _percent_change(before_duration, after_duration)
    rows.insert(
        5,
        {
            "key": "median_completion_time",
            "label": "Median Completion Time",
            "full": round(before_duration, 3),
            "adaptive": round(after_duration, 3),
            "delta": round(duration_delta * 100, 3) if duration_delta is not None else None,
            "format": "seconds",
            "delta_format": "percent_change",
        },
    )
    for key, label, field in (
        ("security_gate_misses", "Security Gate Misses", "mandatory_security_gates_missed"),
        ("approval_bypasses", "Approval Bypasses", "high_risk_approval_bypasses"),
    ):
        before = sum(int(item.get(field, 0) or 0) for item in baseline)
        after = sum(int(item.get(field, 0) or 0) for item in adaptive)
        rows.append(
            {
                "key": key,
                "label": label,
                "full": before,
                "adaptive": after,
                "delta": after - before,
                "format": "integer",
                "delta_format": "absolute",
                "required_adaptive_value": 0,
            }
        )
    order = {
        key: index
        for index, key in enumerate(
            (
                "task_success_rate",
                "pr_success_rate",
                "model_calls_per_task",
                "uncached_input_tokens_per_task",
                "output_tokens_per_task",
                "median_completion_time",
                "roles_executed_per_task",
                "roles_skipped_per_task",
                "context_cache_hit_rate",
                "repair_attempts_per_task",
                "model_escalations_per_task",
                "human_interventions_per_task",
                "security_gate_misses",
                "approval_bypasses",
            )
        )
    }
    return sorted(rows, key=lambda row: order[str(row["key"])])


def _breakdowns(
    baseline: Sequence[Mapping[str, Any]],
    adaptive: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Pre-compute exploratory segments while keeping acceptance checks global."""

    dimensions = ("task_class", "scope", "risk", "repository", "model", "role", "outcome")
    result: dict[str, list[dict[str, Any]]] = {}
    paired = list(zip(baseline, adaptive, strict=True))
    for dimension in dimensions:
        values: set[str] = set()
        for before, after in paired:
            for row in (before, after):
                raw = row.get(dimension, [])
                if isinstance(raw, list):
                    values.update(str(item) for item in raw if str(item))
                elif raw is not None and str(raw):
                    values.add(str(raw))
        segments: list[dict[str, Any]] = []
        for value in sorted(values):
            selected: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
            for before, after in paired:
                memberships: set[str] = set()
                for row in (before, after):
                    raw = row.get(dimension, [])
                    if isinstance(raw, list):
                        memberships.update(str(item) for item in raw)
                    elif raw is not None and str(raw):
                        memberships.add(str(raw))
                if value in memberships:
                    selected.append((before, after))
            segments.append(
                {
                    "value": value,
                    "paired_tasks": len(selected),
                    "comparison": _comparison_rows(
                        [item[0] for item in selected],
                        [item[1] for item in selected],
                    ),
                }
            )
        result[dimension] = segments
    return result


def compare_adaptive_ab(
    baseline: Sequence[Mapping[str, Any]],
    adaptive: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Enforce the milestone's non-compensating efficiency and safety thresholds."""

    if not baseline or len(baseline) != len(adaptive):
        raise EvaluationInputError("adaptive A/B inputs must be non-empty paired runs")
    for before, after in zip(baseline, adaptive, strict=True):
        if before.get("case_id") and before.get("case_id") != after.get("case_id"):
            raise EvaluationInputError("adaptive A/B rows are not paired by case_id")
    efficiency_pairs = [
        (before, after)
        for before, after in zip(baseline, adaptive, strict=True)
        if str(before.get("scope")) in {"trivial", "small", "medium"}
    ]
    efficiency_before = [item[0] for item in efficiency_pairs]
    efficiency_after = [item[1] for item in efficiency_pairs]
    model_call_reduction = _reduction(
        _mean(efficiency_before, "model_calls"),
        _mean(efficiency_after, "model_calls"),
    )
    uncached_token_reduction = _reduction(
        _mean(efficiency_before, "uncached_input_tokens"),
        _mean(efficiency_after, "uncached_input_tokens"),
    )
    baseline_durations = [float(item.get("duration_seconds", 0) or 0) for item in efficiency_before]
    adaptive_durations = [float(item.get("duration_seconds", 0) or 0) for item in efficiency_after]
    duration_reduction = _reduction(
        median(baseline_durations) if baseline_durations else 0.0,
        median(adaptive_durations) if adaptive_durations else 0.0,
    )
    success_regression = _success_rate(baseline) - _success_rate(adaptive)
    security_misses = sum(int(item.get("mandatory_security_gates_missed", 0) or 0) for item in adaptive)
    approval_bypasses = sum(int(item.get("high_risk_approval_bypasses", 0) or 0) for item in adaptive)
    checks = {
        "model_calls_reduced_40_percent": model_call_reduction >= 0.40,
        "uncached_tokens_reduced_30_percent": uncached_token_reduction >= 0.30,
        "median_duration_reduced_25_percent": duration_reduction >= 0.25,
        "success_regression_within_2pp": success_regression <= 0.02,
        "security_sensitive_misses_zero": security_misses == 0,
        "high_risk_approval_bypasses_zero": approval_bypasses == 0,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    status = "pass" if not blockers else "fail"
    comparison = _comparison_rows(baseline, adaptive)
    acceptance_summary = [
        {
            "key": "model_calls",
            "label": "Model calls",
            "value": round(-model_call_reduction * 100, 3),
            "unit": "percent",
            "status": "pass" if checks["model_calls_reduced_40_percent"] else "fail",
        },
        {
            "key": "uncached_tokens",
            "label": "Uncached tokens",
            "value": round(-uncached_token_reduction * 100, 3),
            "unit": "percent",
            "status": "pass" if checks["uncached_tokens_reduced_30_percent"] else "fail",
        },
        {
            "key": "median_latency",
            "label": "Median latency",
            "value": round(-duration_reduction * 100, 3),
            "unit": "percent",
            "status": "pass" if checks["median_duration_reduced_25_percent"] else "fail",
        },
        {
            "key": "success_regression",
            "label": "Success regression",
            "value": round(-success_regression * 100, 3),
            "unit": "percentage_points",
            "status": "pass" if checks["success_regression_within_2pp"] else "fail",
        },
        {
            "key": "security_misses",
            "label": "Security misses",
            "value": security_misses,
            "unit": "integer",
            "status": "pass" if checks["security_sensitive_misses_zero"] else "fail",
        },
        {
            "key": "approval_bypasses",
            "label": "Approval bypasses",
            "value": approval_bypasses,
            "unit": "integer",
            "status": "pass" if checks["high_risk_approval_bypasses_zero"] else "fail",
        },
    ]
    return {
        "schema_version": 1,
        "kind": "adaptive_ab_acceptance",
        "created_at": utc_now(),
        "status": status,
        "adaptive_default_allowed": not blockers,
        "paired_tasks": len(baseline),
        "metrics": {
            "model_call_reduction": round(model_call_reduction, 6),
            "uncached_input_token_reduction": round(uncached_token_reduction, 6),
            "median_duration_reduction": round(duration_reduction, 6),
            "baseline_success_rate": round(_success_rate(baseline), 6),
            "adaptive_success_rate": round(_success_rate(adaptive), 6),
            "success_rate_regression": round(success_regression, 6),
            "mandatory_security_gates_missed": security_misses,
            "high_risk_approval_bypasses": approval_bypasses,
            "quality_score": round(_mean(adaptive, "quality_score"), 6),
            "baseline_quality_score": round(_mean(baseline, "quality_score"), 6),
            "security_score": round(_mean(adaptive, "security_score"), 6),
            "baseline_security_score": round(_mean(baseline, "security_score"), 6),
            "review_score": round(_mean(adaptive, "review_score"), 6),
            "baseline_review_score": round(_mean(baseline, "review_score"), 6),
            "pr_success_rate": round(_mean(adaptive, "pr_success"), 6),
            "baseline_pr_success_rate": round(_mean(baseline, "pr_success"), 6),
            "repair_count": round(_mean(adaptive, "repair_count"), 6),
            "baseline_repair_count": round(_mean(baseline, "repair_count"), 6),
            "human_interventions": round(_mean(adaptive, "human_interventions"), 6),
            "baseline_human_interventions": round(_mean(baseline, "human_interventions"), 6),
        },
        "checks": checks,
        "blockers": blockers,
        "acceptance_summary": acceptance_summary,
        "comparison": comparison,
        "breakdowns": _breakdowns(baseline, adaptive),
    }


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{path} must contain an object")
    return value


def _artifact_score(path: Path, *, status_fields: tuple[str, ...]) -> float:
    if not path.is_file():
        return 0.0
    value = _read_object(path)
    return float(any(value.get(field) in {"pass", "works", "approved"} for field in status_fields))


def collect_adaptive_run(run_dir: Path, case: Mapping[str, Any], *, expected_mode: str) -> dict[str, Any]:
    """Extract A/B metrics only from authoritative persisted run evidence."""

    workflow = _read_object(run_dir / "workflow.json")
    metrics = _read_object(run_dir / "metrics.json")
    actual_mode = str(workflow.get("effective_mode", workflow.get("mode", "")))
    if expected_mode == "full" and actual_mode != "full":
        raise EvaluationInputError(f"{run_dir}: expected full mode, found {actual_mode!r}")
    if expected_mode == "adaptive" and actual_mode not in {"adaptive", "adaptive_safe_fallback"}:
        raise EvaluationInputError(f"{run_dir}: expected adaptive mode, found {actual_mode!r}")
    if str(workflow.get("task_id", "")) != str(case.get("id", "")):
        raise EvaluationInputError(f"{run_dir}: task_id does not match paired dataset case")
    artifacts = run_dir / "artifacts"
    roles = {
        str(item.get("role", ""))
        for item in workflow.get("roles", [])
        if isinstance(item, dict)
    }
    risk = str(case.get("expected_risk", ""))
    category = str(case.get("category", ""))
    approval_grants = workflow.get("approval_grants", [])
    security_required = category == "security_sensitive" or "security-agent" in set(case.get("forbidden_skips", []))
    role_metrics = [item for item in metrics.get("roles", []) if isinstance(item, dict)]
    input_tokens = int(metrics.get("input_tokens_per_task", 0) or 0)
    uncached_input_tokens = int(metrics.get("uncached_input_tokens_per_task", 0) or 0)
    analysis = workflow.get("task_analysis", {})
    task_class = str(analysis.get("task_class", "")) if isinstance(analysis, dict) else ""
    return {
        "case_id": str(case["id"]),
        "task_class": task_class or category,
        "scope": str(case["expected_scope"]),
        "risk": risk,
        "repository": str(workflow.get("project", case.get("repository_profile", ""))),
        "mode": actual_mode,
        "model": sorted(
            {
                str(item.get("model", item.get("execution_profile", "")))
                for item in role_metrics
                if str(item.get("model", item.get("execution_profile", "")))
            }
        ),
        "role": sorted(role for role in roles if role),
        "success": workflow.get("execution_status") == "completed",
        "outcome": "success" if workflow.get("execution_status") == "completed" else "failure",
        "model_calls": int(metrics.get("model_calls_per_task", 0) or 0),
        "input_tokens": input_tokens,
        "uncached_input_tokens": uncached_input_tokens,
        "cached_input_tokens": max(0, input_tokens - uncached_input_tokens),
        "output_tokens": int(metrics.get("output_tokens_per_task", 0) or 0),
        "context_cache_hit_rate": float(metrics.get("context_cache_hit_rate", 0) or 0),
        "roles_executed": int(metrics.get("roles_executed_per_task", len(roles)) or 0),
        "roles_skipped": int(metrics.get("roles_skipped_per_task", 0) or 0),
        "model_escalations": int(metrics.get("model_escalations_per_task", 0) or 0),
        "duration_seconds": float(metrics.get("time_to_success", workflow.get("elapsed_seconds", 0)) or 0),
        "quality_score": _artifact_score(artifacts / "quality.json", status_fields=("overall_status",)),
        "security_score": _artifact_score(artifacts / "security.json", status_fields=("status", "verdict")),
        "review_score": _artifact_score(artifacts / "review.json", status_fields=("status", "verdict")),
        "pr_success": float(_read_object(artifacts / "publication.json").get("pr_created_or_updated") is True) if (artifacts / "publication.json").is_file() else 0.0,
        "repair_count": int(metrics.get("repair_attempts_per_task", 0) or 0),
        "human_interventions": int(metrics.get("human_interventions_per_task", 0) or 0),
        "mandatory_security_gates_missed": int(
            security_required and ("security-agent" not in roles or not (artifacts / "security.json").is_file())
        ),
        "high_risk_approval_bypasses": int(
            risk == "high"
            and workflow.get("execution_status") == "completed"
            and not (isinstance(approval_grants, list) and approval_grants)
        ),
        "run_id": str(workflow.get("run_id", run_dir.name)),
        "run_dir": str(run_dir.resolve()),
    }


def evaluate_paired_adaptive_runs(
    dataset: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    validate_adaptive_dataset(dataset)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("pairs"), list):
        raise EvaluationInputError("paired-run manifest must define schema_version=1 and pairs")
    cases = {str(case["id"]): case for case in dataset["cases"]}
    pairs = manifest["pairs"]
    if len(pairs) != len(cases) or {str(pair.get("case_id", "")) for pair in pairs if isinstance(pair, dict)} != set(cases):
        raise EvaluationInputError("paired-run manifest must cover every golden task exactly once")
    baseline: list[dict[str, Any]] = []
    adaptive: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            raise EvaluationInputError("paired-run entries must be objects")
        case_id = str(pair["case_id"])
        full = collect_adaptive_run(Path(str(pair["full_run_dir"])), cases[case_id], expected_mode="full")
        candidate = collect_adaptive_run(Path(str(pair["adaptive_run_dir"])), cases[case_id], expected_mode="adaptive")
        baseline.append(full)
        adaptive.append(candidate)
        evidence.append({"case_id": case_id, "full": full, "adaptive": candidate})
    report = compare_adaptive_ab(baseline, adaptive)
    report["evidence_kind"] = "paired_authoritative_runs"
    report["dataset_cases"] = len(cases)
    report["pairs"] = evidence
    return report


def acceptance_fingerprint(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
