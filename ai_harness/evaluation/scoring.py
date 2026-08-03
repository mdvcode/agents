"""Score one authoritative Harness run without invoking a model or task command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .io import EvaluationInputError, read_object, require_fields, stable_fingerprint, utc_now


METRIC_NAMES = (
    "planning",
    "code_quality",
    "security",
    "review_quality",
    "pr_success",
    "repair_success",
    "context_quality",
    "latency",
    "tokens",
    "cost",
    "human_interventions",
)


@dataclass(frozen=True)
class RunEvidence:
    root: Path
    workflow: dict[str, Any]
    metrics: dict[str, Any] | None
    plan: str | None
    quality: dict[str, Any] | None
    security: dict[str, Any] | None
    review: dict[str, Any] | None
    verdict: dict[str, Any] | None
    publication: dict[str, Any] | None
    approval: dict[str, Any] | None
    context_logs: tuple[dict[str, Any], ...]


def _optional_object(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    try:
        return read_object(path, required=False)
    except EvaluationInputError as exc:
        warnings.append(str(exc))
        return None


def _read_context_logs(root: Path, warnings: list[str]) -> tuple[dict[str, Any], ...]:
    logs: list[dict[str, Any]] = []
    logs_dir = root / "context-manifests" / "logs"
    if not logs_dir.is_dir():
        return ()
    for path in sorted(logs_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            warnings.append(f"Cannot read {path}: {exc}")
            continue
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.append(f"Invalid JSONL in {path}:{line_number}: {exc}")
                continue
            if isinstance(value, dict):
                logs.append(value)
            else:
                warnings.append(f"Expected an object in {path}:{line_number}")
    return tuple(logs)


def collect_run_evidence(run_dir: Path) -> tuple[RunEvidence, list[str]]:
    root = run_dir.resolve()
    if not root.is_dir():
        raise EvaluationInputError(f"Run directory does not exist: {run_dir}")
    workflow = read_object(root / "workflow.json")
    assert workflow is not None
    warnings: list[str] = []
    plan_path = root / "artifacts" / "plan.md"
    try:
        plan = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else None
    except OSError as exc:
        warnings.append(f"Cannot read {plan_path}: {exc}")
        plan = None
    artifacts = root / "artifacts"
    return (
        RunEvidence(
            root=root,
            workflow=workflow,
            metrics=_optional_object(root / "metrics.json", warnings),
            plan=plan,
            quality=_optional_object(artifacts / "quality.json", warnings),
            security=_optional_object(artifacts / "security.json", warnings),
            review=_optional_object(artifacts / "review.json", warnings),
            verdict=_optional_object(artifacts / "verdict.json", warnings),
            publication=_optional_object(artifacts / "publication.json", warnings),
            approval=_optional_object(artifacts / "approval.json", warnings),
            context_logs=_read_context_logs(root, warnings),
        ),
        warnings,
    )


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def _metric(
    name: str,
    score: float | None,
    *,
    status: str = "scored",
    observed: Any = None,
    evidence: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "score": _clamp(score) if score is not None else None,
        "observed": observed,
        "evidence": evidence or [],
        "warnings": warnings or [],
    }


def _status_score(value: Any, mapping: dict[str, float]) -> float | None:
    return mapping.get(str(value).lower()) if value is not None else None


def _planning(run: RunEvidence, _config: dict[str, Any]) -> dict[str, Any]:
    if run.plan is None:
        return _metric("planning", None, status="unavailable", warnings=["plan.md is missing"])
    headings = ("GOAL", "CONTEXT", "PLAN", "DONE WHEN", "VERIFY")
    present = [heading for heading in headings if f"## {heading}" in run.plan.upper()]
    nonempty = bool(run.plan.strip())
    score = (len(present) / len(headings)) * 0.9 + (0.1 if nonempty else 0.0)
    return _metric(
        "planning",
        score,
        observed={"required_sections": len(headings), "present_sections": present},
        evidence=["artifacts/plan.md"],
    )


def _checks_ratio(value: dict[str, Any]) -> float | None:
    commands = value.get("commands_attempted")
    if not isinstance(commands, list):
        return None
    statuses = [str(item.get("status", "")).lower() for item in commands if isinstance(item, dict)]
    statuses = [status for status in statuses if status]
    if not statuses:
        return None
    return sum(status == "pass" for status in statuses) / len(statuses)


def _code_quality(run: RunEvidence, _config: dict[str, Any]) -> dict[str, Any]:
    if run.quality is None:
        return _metric("code_quality", None, status="unavailable", warnings=["quality.json is missing"])
    overall = _status_score(
        run.quality.get("overall_status", run.quality.get("status")),
        {"pass": 1.0, "warn": 0.65, "fail": 0.0},
    )
    if overall is None:
        return _metric("code_quality", None, status="unavailable", warnings=["quality status is not scorable"])
    ratio = _checks_ratio(run.quality)
    score = overall if ratio is None else (overall * 0.75) + (ratio * 0.25)
    return _metric(
        "code_quality",
        score,
        observed={"overall_status": run.quality.get("overall_status"), "command_pass_ratio": ratio},
        evidence=["artifacts/quality.json"],
    )


def _security(run: RunEvidence, _config: dict[str, Any]) -> dict[str, Any]:
    if run.security is None:
        return _metric("security", None, status="unavailable", warnings=["security.json is missing"])
    severity = str(run.security.get("highest_severity", "")).lower()
    status = str(run.security.get("status", "")).lower()
    score = {"none": 1.0, "low": 0.8, "medium": 0.5, "high": 0.2, "critical": 0.0}.get(severity)
    if status == "fail" and severity in {"", "none"}:
        score = 0.0
    if score is None:
        return _metric("security", None, status="unavailable", warnings=["security severity is not scorable"])
    return _metric(
        "security",
        score,
        observed={"status": status, "highest_severity": severity},
        evidence=["artifacts/security.json"],
    )


def _review_quality(run: RunEvidence, _config: dict[str, Any]) -> dict[str, Any]:
    if run.review is None:
        return _metric("review_quality", None, status="unavailable", warnings=["review.json is missing"])
    status = str(run.review.get("status", "")).lower()
    verdict = str(run.review.get("verdict", "")).lower()
    score = {"pass": 1.0, "warn": 0.65, "fail": 0.0}.get(status)
    if score is None:
        score = {"works": 1.0, "partial": 0.5, "broken": 0.0}.get(verdict)
    if score is None:
        return _metric("review_quality", None, status="unavailable", warnings=["review status is not scorable"])
    findings = run.review.get("findings")
    finding_count = len(findings) if isinstance(findings, list) else 0
    return _metric(
        "review_quality",
        score,
        observed={"status": status, "verdict": verdict, "finding_count": finding_count},
        evidence=["artifacts/review.json"],
    )


def _pr_success(run: RunEvidence, _config: dict[str, Any]) -> dict[str, Any]:
    if run.publication is None:
        return _metric("pr_success", None, status="unavailable", warnings=["publication.json is missing"])
    value = run.publication
    completed = value.get("execution_status") == "completed"
    pr = value.get("pr_created_or_updated") is True and bool(value.get("pr_url"))
    pushed = value.get("branch_pushed") is True
    committed = value.get("commit_created") is True or bool(value.get("commit_sha"))
    score = (0.4 if completed else 0.0) + (0.4 if pr else 0.0) + (0.1 if pushed else 0.0) + (0.1 if committed else 0.0)
    return _metric(
        "pr_success",
        score,
        observed={"completed": completed, "pr": pr, "pushed": pushed, "committed": committed},
        evidence=["artifacts/publication.json"],
    )


def _repair_success(run: RunEvidence, _config: dict[str, Any]) -> dict[str, Any]:
    loops = run.workflow.get("loops")
    if not isinstance(loops, dict):
        return _metric("repair_success", None, status="unavailable", warnings=["workflow loop state is missing"])
    iterations = sum(
        int(value.get("iterations", 0) or 0)
        for value in loops.values()
        if isinstance(value, dict)
    )
    if iterations == 0:
        return _metric(
            "repair_success",
            None,
            status="not_applicable",
            observed={"iterations": 0},
            evidence=["workflow.json"],
        )
    final_statuses = [
        str(value.get("overall_status", value.get("status", ""))).lower()
        for value in (run.quality, run.security, run.review)
        if isinstance(value, dict)
    ]
    passed = bool(final_statuses) and all(status == "pass" for status in final_statuses)
    return _metric(
        "repair_success",
        1.0 if passed else 0.0,
        observed={"iterations": iterations, "final_gate_statuses": final_statuses},
        evidence=["workflow.json", "artifacts/quality.json", "artifacts/security.json", "artifacts/review.json"],
    )


def _context_quality(run: RunEvidence, _config: dict[str, Any]) -> dict[str, Any]:
    if not run.context_logs:
        return _metric("context_quality", None, status="unavailable", warnings=["context provenance logs are missing"])
    valid = 0
    bounded = 0
    nonempty = 0
    for log in run.context_logs:
        budget = log.get("budget")
        selected = log.get("selected")
        if isinstance(budget, dict) and isinstance(selected, list):
            valid += 1
            total = int(budget.get("total_tokens", 0) or 0)
            used = int(budget.get("used_tokens", 0) or 0)
            bounded += int(total > 0 and 0 <= used <= total)
            nonempty += int(bool(selected))
    if valid == 0:
        return _metric("context_quality", None, status="unavailable", warnings=["context logs are not scorable"])
    score = (bounded / valid) * 0.6 + (nonempty / valid) * 0.4
    return _metric(
        "context_quality",
        score,
        observed={"log_count": len(run.context_logs), "valid_logs": valid, "bounded_logs": bounded, "nonempty_logs": nonempty},
        evidence=["context-manifests/logs/*.jsonl"],
    )


def _lower_is_better(name: str, observed: float | None, config: dict[str, Any], evidence: str) -> dict[str, Any]:
    if observed is None:
        return _metric(name, None, status="unavailable", warnings=[f"{name} telemetry is missing"])
    target = float(config.get("target", 0))
    worst = float(config.get("worst", target))
    if worst <= target:
        return _metric(name, None, status="unavailable", warnings=[f"{name} rubric requires worst > target"])
    score = 1.0 if observed <= target else 1.0 - ((observed - target) / (worst - target))
    return _metric(name, score, observed=observed, evidence=[evidence])


def _latency(run: RunEvidence, config: dict[str, Any]) -> dict[str, Any]:
    value = run.metrics.get("duration_ms") if run.metrics else None
    observed = float(value) if isinstance(value, (int, float)) else None
    return _lower_is_better("latency", observed, config, "metrics.json")


def _tokens(run: RunEvidence, config: dict[str, Any]) -> dict[str, Any]:
    value = run.metrics.get("tokens_used") if run.metrics else run.workflow.get("tokens_used")
    observed = float(value) if isinstance(value, (int, float)) else None
    return _lower_is_better("tokens", observed, config, "metrics.json")


def _estimated_cost(run: RunEvidence, config: dict[str, Any]) -> tuple[float | None, str]:
    if run.metrics and isinstance(run.metrics.get("cost_usd"), (int, float)):
        return float(run.metrics["cost_usd"]), "reported"
    pricing = config.get("pricing_per_million_tokens")
    roles = run.metrics.get("roles") if run.metrics else None
    if not isinstance(pricing, dict) or not isinstance(roles, list):
        return None, "unavailable"
    required_rates = ("input", "cached_input", "output")
    if not all(isinstance(pricing.get(key), (int, float)) for key in required_rates):
        return None, "unavailable"
    input_tokens = sum(int(role.get("input_tokens", 0) or 0) for role in roles if isinstance(role, dict))
    cached_tokens = sum(int(role.get("cached_input_tokens", 0) or 0) for role in roles if isinstance(role, dict))
    output_tokens = sum(int(role.get("output_tokens", 0) or 0) for role in roles if isinstance(role, dict))
    uncached = max(0, input_tokens - cached_tokens)
    cost = (
        uncached * float(pricing["input"])
        + cached_tokens * float(pricing["cached_input"])
        + output_tokens * float(pricing["output"])
    ) / 1_000_000
    return cost, "estimated"


def _cost(run: RunEvidence, config: dict[str, Any]) -> dict[str, Any]:
    observed, source = _estimated_cost(run, config)
    result = _lower_is_better("cost", observed, config, "metrics.json")
    result["observed"] = {"usd": observed, "source": source}
    return result


def _human_interventions(run: RunEvidence, config: dict[str, Any]) -> dict[str, Any]:
    grants = run.workflow.get("approval_grants")
    grant_count = len(grants) if isinstance(grants, list) else 0
    resume_count = int(run.workflow.get("resume_count", 0) or 0)
    approval_count = 1 if run.approval is not None else 0
    interventions = max(grant_count, resume_count, approval_count)
    result = _lower_is_better("human_interventions", float(interventions), config, "workflow.json")
    result["observed"] = interventions
    if run.approval is not None:
        result["evidence"].append("artifacts/approval.json")
    return result


SCORERS: dict[str, Callable[[RunEvidence, dict[str, Any]], dict[str, Any]]] = {
    "planning": _planning,
    "code_quality": _code_quality,
    "security": _security,
    "review_quality": _review_quality,
    "pr_success": _pr_success,
    "repair_success": _repair_success,
    "context_quality": _context_quality,
    "latency": _latency,
    "tokens": _tokens,
    "cost": _cost,
    "human_interventions": _human_interventions,
}


def validate_rubric(rubric: dict[str, Any]) -> None:
    require_fields(rubric, ("schema_version", "name", "minimum_coverage", "metrics"), label="rubric")
    if rubric.get("schema_version") != 1:
        raise EvaluationInputError("rubric.schema_version must be 1")
    minimum_coverage = rubric.get("minimum_coverage")
    if not isinstance(minimum_coverage, (int, float)) or not 0 <= float(minimum_coverage) <= 1:
        raise EvaluationInputError("rubric.minimum_coverage must be between 0 and 1")
    metrics = rubric.get("metrics")
    if not isinstance(metrics, dict):
        raise EvaluationInputError("rubric.metrics must be an object")
    unknown = sorted(set(metrics) - set(METRIC_NAMES))
    missing = sorted(set(METRIC_NAMES) - set(metrics))
    if unknown or missing:
        details = []
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        if missing:
            details.append("missing: " + ", ".join(missing))
        raise EvaluationInputError("rubric metric set is invalid (" + "; ".join(details) + ")")
    total_weight = 0.0
    for name, config in metrics.items():
        if not isinstance(config, dict) or not isinstance(config.get("weight"), (int, float)):
            raise EvaluationInputError(f"rubric metric {name!r} requires numeric weight")
        if float(config["weight"]) < 0:
            raise EvaluationInputError(f"rubric metric {name!r} weight cannot be negative")
        if not isinstance(config.get("required"), bool):
            raise EvaluationInputError(f"rubric metric {name!r} requires boolean required")
        total_weight += float(config["weight"])
    if total_weight <= 0:
        raise EvaluationInputError("rubric total metric weight must be positive")


def score_run(
    run_dir: Path,
    rubric: dict[str, Any],
    *,
    label: str | None = None,
    variant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_rubric(rubric)
    run, warnings = collect_run_evidence(run_dir)
    metric_configs = rubric["metrics"]
    results: list[dict[str, Any]] = []
    total_weight = 0.0
    scored_weight = 0.0
    weighted_score = 0.0
    for name in METRIC_NAMES:
        config = metric_configs[name]
        weight = float(config.get("weight", 0.0))
        result = SCORERS[name](run, config)
        result["weight"] = weight
        result["required"] = bool(config.get("required", False))
        results.append(result)
        total_weight += weight
        if result["status"] == "scored" and result["score"] is not None:
            scored_weight += weight
            weighted_score += float(result["score"]) * weight
    coverage = (scored_weight / total_weight) if total_weight else 0.0
    overall = (weighted_score / scored_weight) if scored_weight else None
    required_unavailable = [
        result["name"]
        for result in results
        if result["required"] and result["status"] != "scored"
    ]
    minimum_coverage = float(rubric["minimum_coverage"])
    status = "pass"
    blockers: list[str] = []
    if required_unavailable:
        blockers.append("required metrics unavailable: " + ", ".join(required_unavailable))
    if coverage < minimum_coverage:
        blockers.append(f"coverage {coverage:.3f} is below minimum {minimum_coverage:.3f}")
    if blockers:
        status = "insufficient_evidence"
    workflow = run.workflow
    workflow_variant = workflow.get("eval_variant", {})
    resolved_variant = dict(workflow_variant) if isinstance(workflow_variant, dict) else {}
    if variant is not None:
        resolved_variant.update(variant)
    subject = {
        "run_id": str(workflow.get("run_id", run.root.name)),
        "label": label or str(workflow.get("run_id", run.root.name)),
        "path": str(run.root),
        "project_profile": str(workflow.get("project_profile", workflow.get("project", ""))),
        "runtime": workflow.get("runtime", workflow.get("executor", {})),
        "variant": resolved_variant,
    }
    return {
        "schema_version": 1,
        "kind": "scorecard",
        "created_at": utc_now(),
        "rubric": {"name": rubric["name"], "fingerprint": stable_fingerprint(rubric)},
        "subject": subject,
        "status": status,
        "overall_score": round(overall, 6) if overall is not None else None,
        "coverage": round(coverage, 6),
        "minimum_coverage": minimum_coverage,
        "metrics": results,
        "required_unavailable": required_unavailable,
        "blockers": blockers,
        "warnings": warnings,
    }
