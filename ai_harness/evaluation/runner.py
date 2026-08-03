"""Evaluate versioned dataset cases against explicit run subjects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import EvaluationInputError, require_fields, stable_fingerprint, utc_now
from .scoring import METRIC_NAMES, score_run


def validate_dataset(dataset: dict[str, Any]) -> None:
    require_fields(dataset, ("schema_version", "name", "cases"), label="dataset")
    if dataset.get("schema_version") != 1:
        raise EvaluationInputError("dataset.schema_version must be 1")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationInputError("dataset.cases must be a non-empty array")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvaluationInputError(f"dataset case {index} must be an object")
        require_fields(case, ("id", "subject", "expectations"), label=f"dataset case {index}")
        case_id = str(case["id"])
        if case_id in seen:
            raise EvaluationInputError(f"duplicate dataset case id: {case_id}")
        seen.add(case_id)
        if not isinstance(case["expectations"], dict):
            raise EvaluationInputError(f"dataset case {case_id!r} expectations must be an object")
        allowed_expectations = {
            "minimum_overall_score",
            "minimum_coverage",
            "required_metrics",
            "minimum_metric_scores",
        }
        unknown = sorted(set(case["expectations"]) - allowed_expectations)
        if unknown:
            raise EvaluationInputError(
                f"dataset case {case_id!r} has unknown expectations: {', '.join(unknown)}"
            )
        required_metrics = case["expectations"].get("required_metrics", [])
        if not isinstance(required_metrics, list) or any(
            str(metric) not in METRIC_NAMES for metric in required_metrics
        ):
            raise EvaluationInputError(f"dataset case {case_id!r} has invalid required_metrics")
        minimum_metrics = case["expectations"].get("minimum_metric_scores", {})
        if not isinstance(minimum_metrics, dict) or any(
            str(metric) not in METRIC_NAMES for metric in minimum_metrics
        ):
            raise EvaluationInputError(f"dataset case {case_id!r} has invalid minimum_metric_scores")
        for field in ("minimum_overall_score", "minimum_coverage"):
            threshold = case["expectations"].get(field)
            if threshold is not None and (
                not isinstance(threshold, (int, float)) or not 0 <= float(threshold) <= 1
            ):
                raise EvaluationInputError(
                    f"dataset case {case_id!r} {field} must be between 0 and 1"
                )


def _metric_map(scorecard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(metric.get("name")): metric
        for metric in scorecard.get("metrics", [])
        if isinstance(metric, dict)
    }


def evaluate_expectations(scorecard: dict[str, Any], expectations: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    overall = scorecard.get("overall_score")
    minimum_overall = expectations.get("minimum_overall_score")
    if isinstance(minimum_overall, (int, float)):
        if not isinstance(overall, (int, float)) or float(overall) < float(minimum_overall):
            failures.append(f"overall score {overall!r} is below {float(minimum_overall):.3f}")
    minimum_coverage = expectations.get("minimum_coverage")
    coverage = scorecard.get("coverage")
    if isinstance(minimum_coverage, (int, float)):
        if not isinstance(coverage, (int, float)) or float(coverage) < float(minimum_coverage):
            failures.append(f"coverage {coverage!r} is below {float(minimum_coverage):.3f}")
    metrics = _metric_map(scorecard)
    required_metrics = expectations.get("required_metrics", [])
    if isinstance(required_metrics, list):
        for name in required_metrics:
            metric = metrics.get(str(name))
            if metric is None or metric.get("status") != "scored":
                failures.append(f"required metric {name!r} is unavailable")
    minimum_metrics = expectations.get("minimum_metric_scores", {})
    if isinstance(minimum_metrics, dict):
        for name, threshold in minimum_metrics.items():
            metric = metrics.get(str(name))
            score = metric.get("score") if metric else None
            if not isinstance(threshold, (int, float)):
                failures.append(f"minimum score for {name!r} is not numeric")
            elif not isinstance(score, (int, float)) or float(score) < float(threshold):
                failures.append(f"metric {name!r} score {score!r} is below {float(threshold):.3f}")
    return failures


def evaluate_dataset(
    dataset: dict[str, Any],
    rubric: dict[str, Any],
    subjects: dict[str, Path],
    variants: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_dataset(dataset)
    case_results: list[dict[str, Any]] = []
    blockers: list[str] = []
    for case in dataset["cases"]:
        subject_id = str(case["subject"])
        run_dir = subjects.get(subject_id)
        if run_dir is None:
            failures = [f"subject mapping is missing: {subject_id}"]
            scorecard = None
        else:
            scorecard = score_run(
                run_dir,
                rubric,
                label=subject_id,
                variant=(variants or {}).get(subject_id),
            )
            failures = evaluate_expectations(scorecard, case["expectations"])
        case_result = {
            "id": str(case["id"]),
            "subject": subject_id,
            "tags": case.get("tags", []),
            "status": "pass" if not failures else "fail",
            "failures": failures,
            "scorecard": scorecard,
        }
        case_results.append(case_result)
        blockers.extend(f"{case['id']}: {failure}" for failure in failures)
    return {
        "schema_version": 1,
        "kind": "evaluation_run",
        "created_at": utc_now(),
        "dataset": {"name": dataset["name"], "fingerprint": stable_fingerprint(dataset)},
        "rubric": {"name": rubric["name"], "fingerprint": stable_fingerprint(rubric)},
        "status": "pass" if not blockers else "fail",
        "case_count": len(case_results),
        "passed_cases": sum(result["status"] == "pass" for result in case_results),
        "cases": case_results,
        "blockers": blockers,
    }
