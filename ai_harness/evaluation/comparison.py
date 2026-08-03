"""Paired comparison of compatible evaluation reports."""

from __future__ import annotations

from typing import Any

from .io import EvaluationInputError, utc_now


def _scorecards(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if report.get("kind") == "scorecard":
        return {"single": report}
    cards: dict[str, dict[str, Any]] = {}
    for case in report.get("cases", []):
        if not isinstance(case, dict) or not isinstance(case.get("scorecard"), dict):
            continue
        cards[str(case.get("id", case.get("subject", "case")))] = case["scorecard"]
    return cards


def _metric_scores(scorecard: dict[str, Any]) -> dict[str, float]:
    return {
        str(metric["name"]): float(metric["score"])
        for metric in scorecard.get("metrics", [])
        if isinstance(metric, dict)
        and metric.get("status") == "scored"
        and isinstance(metric.get("score"), (int, float))
    }


def compare_reports(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    regression_tolerance: float = 0.02,
    minimum_coverage: float = 0.6,
) -> dict[str, Any]:
    if regression_tolerance < 0:
        raise EvaluationInputError("regression_tolerance cannot be negative")
    if not 0 <= minimum_coverage <= 1:
        raise EvaluationInputError("minimum_coverage must be between 0 and 1")
    baseline_rubric = baseline.get("rubric", {})
    candidate_rubric = candidate.get("rubric", {})
    if baseline_rubric.get("fingerprint") != candidate_rubric.get("fingerprint"):
        raise EvaluationInputError("reports use different rubric fingerprints")
    baseline_dataset = baseline.get("dataset")
    candidate_dataset = candidate.get("dataset")
    if baseline_dataset is not None or candidate_dataset is not None:
        if not isinstance(baseline_dataset, dict) or not isinstance(candidate_dataset, dict):
            raise EvaluationInputError("cannot compare a dataset report with a single scorecard")
        if baseline_dataset.get("fingerprint") != candidate_dataset.get("fingerprint"):
            raise EvaluationInputError("reports use different dataset fingerprints")
    baseline_cards = _scorecards(baseline)
    candidate_cards = _scorecards(candidate)
    if set(baseline_cards) != set(candidate_cards):
        missing = sorted(set(baseline_cards) - set(candidate_cards))
        extra = sorted(set(candidate_cards) - set(baseline_cards))
        raise EvaluationInputError(
            f"reports do not contain identical paired cases; missing={missing}, extra={extra}"
        )
    shared = sorted(set(baseline_cards) & set(candidate_cards))
    if not shared:
        raise EvaluationInputError("reports have no paired scorecards")
    cases: list[dict[str, Any]] = []
    blockers: list[str] = []
    total_baseline = 0.0
    total_candidate = 0.0
    overall_pairs = 0
    for case_id in shared:
        before = baseline_cards[case_id]
        after = candidate_cards[case_id]
        before_coverage = float(before.get("coverage", 0.0) or 0.0)
        after_coverage = float(after.get("coverage", 0.0) or 0.0)
        case_blockers: list[str] = []
        if before_coverage < minimum_coverage or after_coverage < minimum_coverage:
            case_blockers.append("paired coverage is below the comparison minimum")
        before_metrics = _metric_scores(before)
        after_metrics = _metric_scores(after)
        deltas = []
        missing_metrics = sorted(set(before_metrics) - set(after_metrics))
        for name in missing_metrics:
            case_blockers.append(f"candidate lost scored metric {name}")
        for name in sorted(set(before_metrics) & set(after_metrics)):
            delta = after_metrics[name] - before_metrics[name]
            regression = delta < -regression_tolerance
            if regression:
                case_blockers.append(f"metric {name} regressed by {delta:.3f}")
            deltas.append(
                {
                    "name": name,
                    "baseline": round(before_metrics[name], 6),
                    "candidate": round(after_metrics[name], 6),
                    "delta": round(delta, 6),
                    "regression": regression,
                }
            )
        before_overall = before.get("overall_score")
        after_overall = after.get("overall_score")
        overall_delta = None
        if isinstance(before_overall, (int, float)) and isinstance(after_overall, (int, float)):
            overall_delta = float(after_overall) - float(before_overall)
            total_baseline += float(before_overall)
            total_candidate += float(after_overall)
            overall_pairs += 1
            if overall_delta < -regression_tolerance:
                case_blockers.append(f"overall score regressed by {overall_delta:.3f}")
        cases.append(
            {
                "id": case_id,
                "status": "pass" if not case_blockers else "regression",
                "baseline_coverage": before_coverage,
                "candidate_coverage": after_coverage,
                "overall_delta": round(overall_delta, 6) if overall_delta is not None else None,
                "metrics": deltas,
                "blockers": case_blockers,
            }
        )
        blockers.extend(f"{case_id}: {message}" for message in case_blockers)
    aggregate_delta = (
        (total_candidate / overall_pairs) - (total_baseline / overall_pairs)
        if overall_pairs
        else None
    )
    return {
        "schema_version": 1,
        "kind": "evaluation_comparison",
        "created_at": utc_now(),
        "status": "pass" if not blockers else "regression",
        "regression_tolerance": regression_tolerance,
        "minimum_coverage": minimum_coverage,
        "paired_cases": len(shared),
        "aggregate_score_delta": round(aggregate_delta, 6) if aggregate_delta is not None else None,
        "cases": cases,
        "blockers": blockers,
    }
