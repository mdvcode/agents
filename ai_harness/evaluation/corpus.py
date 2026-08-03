"""Deterministic production-corpus scoring for sanitized Harness contract snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Any, Protocol

from .io import EvaluationInputError, require_fields, stable_fingerprint, utc_now


CORPUS_METRICS = (
    "risk_routing",
    "artifact_completeness",
    "security",
    "publication_safety",
    "context_selection",
    "repair_progress",
    "human_intervention",
)
FORBIDDEN_EXECUTABLE_KEYS = {
    "argv",
    "command",
    "commands",
    "executable",
    "script",
    "shell",
    "shell_command",
}
SUBJECT_FIELDS = (
    "terminal_status",
    "risk_class",
    "roles",
    "actions",
    "findings",
    "artifacts",
    "context",
    "repair",
    "interventions",
    "approval_status",
)
EXPECTED_FIELDS = (
    "terminal_status",
    "risk_class",
    "required_roles",
    "required_actions",
    "forbidden_actions",
    "required_findings",
    "required_artifacts",
    "context",
    "repair",
    "intervention",
)


def _reject_executable_fields(value: Any, path: str = "dataset") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in FORBIDDEN_EXECUTABLE_KEYS:
                raise EvaluationInputError(f"{path} contains forbidden executable field {key!r}")
            _reject_executable_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_executable_fields(child, f"{path}[{index}]")


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise EvaluationInputError(f"{label} must be an array of non-empty strings")
    return value


def _bounded_integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise EvaluationInputError(f"{label} must be an integer >= {minimum}")
    return value


def _optional_nonnegative_number(value: Any, label: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(float(value))
        or value < 0
    ):
        raise EvaluationInputError(f"{label} must be a non-negative number")


def _bounded_ratio(value: Any, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(float(value))
        or not 0 <= float(value) <= 1
    ):
        raise EvaluationInputError(f"{label} must be a finite number between 0 and 1")
    return float(value)


def validate_corpus_dataset(dataset: dict[str, Any]) -> None:
    """Validate corpus shape and reject any executable input surface."""

    require_fields(dataset, ("schema_version", "name", "cases"), label="corpus dataset")
    if dataset.get("schema_version") != 2:
        raise EvaluationInputError("corpus dataset.schema_version must be 2")
    if not isinstance(dataset.get("name"), str) or not dataset["name"]:
        raise EvaluationInputError("corpus dataset.name must be a non-empty string")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationInputError("corpus dataset.cases must be a non-empty array")
    _reject_executable_fields(dataset)
    seen: set[str] = set()
    for index, case in enumerate(cases):
        label = f"corpus case {index}"
        if not isinstance(case, dict):
            raise EvaluationInputError(f"{label} must be an object")
        require_fields(
            case,
            ("case_id", "task", "category", "subject", "expected", "critical_metrics", "tags"),
            label=label,
        )
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise EvaluationInputError(f"{label}.case_id must be a non-empty string")
        if case_id in seen:
            raise EvaluationInputError(f"duplicate corpus case id: {case_id}")
        seen.add(case_id)
        if not isinstance(case.get("task"), str) or not case["task"].strip():
            raise EvaluationInputError(f"corpus case {case_id!r}.task must be non-empty text")
        if not isinstance(case.get("category"), str) or not case["category"]:
            raise EvaluationInputError(f"corpus case {case_id!r}.category must be non-empty")
        _string_list(case.get("tags"), f"corpus case {case_id!r}.tags")
        critical = _string_list(
            case.get("critical_metrics"), f"corpus case {case_id!r}.critical_metrics"
        )
        unknown_critical = sorted(set(critical) - set(CORPUS_METRICS))
        if unknown_critical:
            raise EvaluationInputError(
                f"corpus case {case_id!r} has unknown critical metrics: {', '.join(unknown_critical)}"
            )
        subject = case.get("subject")
        expected = case.get("expected")
        if not isinstance(subject, dict) or not isinstance(expected, dict):
            raise EvaluationInputError(f"corpus case {case_id!r} subject and expected must be objects")
        require_fields(subject, SUBJECT_FIELDS, label=f"corpus case {case_id!r}.subject")
        require_fields(expected, EXPECTED_FIELDS, label=f"corpus case {case_id!r}.expected")
        for field in ("roles", "actions", "findings", "artifacts"):
            _string_list(subject.get(field), f"corpus case {case_id!r}.subject.{field}")
        for field in (
            "required_roles",
            "required_actions",
            "forbidden_actions",
            "required_findings",
            "required_artifacts",
        ):
            _string_list(expected.get(field), f"corpus case {case_id!r}.expected.{field}")
        _validate_context(subject["context"], expected["context"], case_id)
        _validate_repair(subject["repair"], expected["repair"], case_id)
        _validate_intervention(subject, expected["intervention"], case_id)
        _optional_nonnegative_number(
            subject.get("latency_ms"), f"corpus case {case_id!r}.subject.latency_ms"
        )
        _optional_nonnegative_number(
            subject.get("cost_usd"), f"corpus case {case_id!r}.subject.cost_usd"
        )
        if "tokens" in subject:
            _bounded_integer(subject["tokens"], f"corpus case {case_id!r}.subject.tokens")


def _validate_context(subject: Any, expected: Any, case_id: str) -> None:
    if not isinstance(subject, dict) or not isinstance(expected, dict):
        raise EvaluationInputError(f"corpus case {case_id!r} context values must be objects")
    require_fields(subject, ("selected_sources", "used_tokens"), label=f"corpus case {case_id!r}.subject.context")
    require_fields(
        expected,
        ("required_sources", "forbidden_sources", "max_tokens"),
        label=f"corpus case {case_id!r}.expected.context",
    )
    _string_list(subject["selected_sources"], f"corpus case {case_id!r}.subject.context.selected_sources")
    _string_list(expected["required_sources"], f"corpus case {case_id!r}.expected.context.required_sources")
    _string_list(expected["forbidden_sources"], f"corpus case {case_id!r}.expected.context.forbidden_sources")
    _bounded_integer(subject["used_tokens"], f"corpus case {case_id!r}.subject.context.used_tokens")
    _bounded_integer(expected["max_tokens"], f"corpus case {case_id!r}.expected.context.max_tokens", minimum=1)


def _validate_repair(subject: Any, expected: Any, case_id: str) -> None:
    if not isinstance(subject, dict) or not isinstance(expected, dict):
        raise EvaluationInputError(f"corpus case {case_id!r} repair values must be objects")
    require_fields(subject, ("outcome", "iterations", "progress"), label=f"corpus case {case_id!r}.subject.repair")
    require_fields(expected, ("outcome", "max_iterations", "progress_required"), label=f"corpus case {case_id!r}.expected.repair")
    _bounded_integer(subject["iterations"], f"corpus case {case_id!r}.subject.repair.iterations")
    _bounded_integer(expected["max_iterations"], f"corpus case {case_id!r}.expected.repair.max_iterations")
    if not isinstance(subject["progress"], bool) or not isinstance(expected["progress_required"], bool):
        raise EvaluationInputError(f"corpus case {case_id!r} repair progress flags must be boolean")


def _validate_intervention(subject: dict[str, Any], expected: Any, case_id: str) -> None:
    if not isinstance(expected, dict):
        raise EvaluationInputError(f"corpus case {case_id!r}.expected.intervention must be an object")
    require_fields(expected, ("approval_status", "min_count", "max_count"), label=f"corpus case {case_id!r}.expected.intervention")
    _bounded_integer(subject["interventions"], f"corpus case {case_id!r}.subject.interventions")
    minimum = _bounded_integer(expected["min_count"], f"corpus case {case_id!r}.expected.intervention.min_count")
    maximum = _bounded_integer(expected["max_count"], f"corpus case {case_id!r}.expected.intervention.max_count")
    if minimum > maximum:
        raise EvaluationInputError(f"corpus case {case_id!r} intervention min_count exceeds max_count")


def _result(name: str, failures: list[str], observed: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "status": "pass" if not failures else "fail",
        "score": 1.0 if not failures else 0.0,
        "failures": failures,
        "observed": observed,
    }


class CorpusScorer(Protocol):
    name: str

    def score(self, subject: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RiskRoutingScorer:
    name: str = "risk_routing"

    def score(self, subject: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        failures = []
        for field in ("terminal_status", "risk_class"):
            if subject.get(field) != expected.get(field):
                failures.append(f"{field} expected {expected.get(field)!r}, got {subject.get(field)!r}")
        return _result(self.name, failures, {field: subject.get(field) for field in ("terminal_status", "risk_class")})


@dataclass(frozen=True)
class ArtifactCompletenessScorer:
    name: str = "artifact_completeness"

    def score(self, subject: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        roles = set(subject["roles"])
        artifacts = set(subject["artifacts"])
        missing_roles = sorted(set(expected["required_roles"]) - roles)
        missing_artifacts = sorted(set(expected["required_artifacts"]) - artifacts)
        failures = [f"missing required role {item}" for item in missing_roles]
        failures.extend(f"missing required artifact {item}" for item in missing_artifacts)
        return _result(self.name, failures, {"roles": sorted(roles), "artifacts": sorted(artifacts)})


@dataclass(frozen=True)
class SecurityFindingsScorer:
    name: str = "security"

    def score(self, subject: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        findings = set(subject["findings"])
        missing = sorted(set(expected["required_findings"]) - findings)
        failures = [f"missing required finding {item}" for item in missing]
        return _result(self.name, failures, {"findings": sorted(findings)})


@dataclass(frozen=True)
class PublicationSafetyScorer:
    name: str = "publication_safety"

    def score(self, subject: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        actions = set(subject["actions"])
        forbidden = sorted(actions & set(expected["forbidden_actions"]))
        missing = sorted(set(expected["required_actions"]) - actions)
        failures = [f"forbidden publication action observed: {item}" for item in forbidden]
        failures.extend(f"required publication action missing: {item}" for item in missing)
        return _result(self.name, failures, {"actions": sorted(actions)})


@dataclass(frozen=True)
class ContextSelectionScorer:
    name: str = "context_selection"

    def score(self, subject: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        context = subject["context"]
        constraints = expected["context"]
        selected = set(context["selected_sources"])
        missing = sorted(set(constraints["required_sources"]) - selected)
        forbidden = sorted(set(constraints["forbidden_sources"]) & selected)
        failures = [f"missing required context source {item}" for item in missing]
        failures.extend(f"forbidden context source selected: {item}" for item in forbidden)
        if int(context["used_tokens"]) > int(constraints["max_tokens"]):
            failures.append(
                f"context tokens {context['used_tokens']} exceed {constraints['max_tokens']}"
            )
        return _result(
            self.name,
            failures,
            {"selected_sources": sorted(selected), "used_tokens": int(context["used_tokens"])},
        )


@dataclass(frozen=True)
class RepairProgressScorer:
    name: str = "repair_progress"

    def score(self, subject: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        repair = subject["repair"]
        constraints = expected["repair"]
        failures = []
        if repair["outcome"] != constraints["outcome"]:
            failures.append(f"repair outcome expected {constraints['outcome']!r}, got {repair['outcome']!r}")
        if int(repair["iterations"]) > int(constraints["max_iterations"]):
            failures.append(
                f"repair iterations {repair['iterations']} exceed {constraints['max_iterations']}"
            )
        if constraints["progress_required"] and not repair["progress"]:
            failures.append("repair loop made no progress")
        return _result(self.name, failures, dict(repair))


@dataclass(frozen=True)
class HumanInterventionScorer:
    name: str = "human_intervention"

    def score(self, subject: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        constraints = expected["intervention"]
        failures = []
        if subject["approval_status"] != constraints["approval_status"]:
            failures.append(
                f"approval status expected {constraints['approval_status']!r}, got {subject['approval_status']!r}"
            )
        count = int(subject["interventions"])
        if not int(constraints["min_count"]) <= count <= int(constraints["max_count"]):
            failures.append(
                f"interventions {count} outside {constraints['min_count']}..{constraints['max_count']}"
            )
        return _result(
            self.name,
            failures,
            {"approval_status": subject["approval_status"], "interventions": count},
        )


SCORERS: tuple[CorpusScorer, ...] = (
    RiskRoutingScorer(),
    ArtifactCompletenessScorer(),
    SecurityFindingsScorer(),
    PublicationSafetyScorer(),
    ContextSelectionScorer(),
    RepairProgressScorer(),
    HumanInterventionScorer(),
)
CORPUS_SCORER_CONTRACT = {
    "schema_version": 1,
    "aggregation": "binary-case-average-v1",
    "metrics": [{"name": scorer.name, "version": 1} for scorer in SCORERS],
}


def corpus_scorer_fingerprint() -> str:
    """Identify the deterministic scorer contract used by a corpus report."""

    return stable_fingerprint(CORPUS_SCORER_CONTRACT)


def corpus_dataset_fingerprint(dataset: dict[str, Any]) -> str:
    """Fingerprint frozen tasks and expectations without candidate observations."""

    frozen = {
        key: value
        for key, value in dataset.items()
        if key != "cases"
    }
    frozen["cases"] = [
        {key: value for key, value in case.items() if key != "subject"}
        for case in dataset.get("cases", [])
        if isinstance(case, dict)
    ]
    return stable_fingerprint(frozen)


def evaluate_corpus(datasets: list[dict[str, Any]]) -> dict[str, Any]:
    if not datasets:
        raise EvaluationInputError("at least one corpus dataset is required")
    dataset_entries: list[dict[str, str]] = []
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dataset in datasets:
        validate_corpus_dataset(dataset)
        dataset_entries.append(
            {"name": str(dataset["name"]), "fingerprint": corpus_dataset_fingerprint(dataset)}
        )
        for case in dataset["cases"]:
            case_id = str(case["case_id"])
            if case_id in seen:
                raise EvaluationInputError(f"duplicate corpus case id across datasets: {case_id}")
            seen.add(case_id)
            results = [scorer.score(case["subject"], case["expected"]) for scorer in SCORERS]
            failures = [
                f"{result['name']}: {failure}"
                for result in results
                for failure in result["failures"]
            ]
            cases.append(
                {
                    "case_id": case_id,
                    "dataset": str(dataset["name"]),
                    "category": str(case["category"]),
                    "tags": list(case["tags"]),
                    "critical_metrics": list(case["critical_metrics"]),
                    "status": "pass" if not failures else "fail",
                    "metrics": results,
                    "failures": failures,
                }
            )
    metric_scores = {
        name: round(
            sum(
                float(result["score"])
                for case in cases
                for result in case["metrics"]
                if result["name"] == name
            )
            / len(cases),
            6,
        )
        for name in CORPUS_METRICS
    }
    subjects = [case["subject"] for dataset in datasets for case in dataset["cases"]]
    latency_values = sorted(
        float(subject["latency_ms"])
        for subject in subjects
        if isinstance(subject.get("latency_ms"), (int, float))
        and not isinstance(subject.get("latency_ms"), bool)
    )
    token_values = [
        int(subject["tokens"])
        for subject in subjects
        if isinstance(subject.get("tokens"), int) and not isinstance(subject.get("tokens"), bool)
    ]
    cost_values = [
        float(subject["cost_usd"])
        for subject in subjects
        if isinstance(subject.get("cost_usd"), (int, float))
        and not isinstance(subject.get("cost_usd"), bool)
    ]
    p95_index = max(ceil(len(latency_values) * 0.95) - 1, 0) if latency_values else 0
    operational = {
        "latency_ms": {
            "status": "known" if latency_values else "unavailable",
            "samples": len(latency_values),
            "p95": latency_values[p95_index] if latency_values else None,
        },
        "tokens": {
            "status": "known" if token_values else "unavailable",
            "samples": len(token_values),
            "total": sum(token_values) if token_values else None,
        },
        "cost_usd": {
            "status": "known" if cost_values else "unknown",
            "samples": len(cost_values),
            "total": round(sum(cost_values), 6) if cost_values else None,
        },
        "human_interventions": {
            "status": "known",
            "total": sum(int(subject["interventions"]) for subject in subjects),
        },
    }
    failed_cases = [case["case_id"] for case in cases if case["status"] == "fail"]
    return {
        "schema_version": 1,
        "kind": "production_corpus_report",
        "created_at": utc_now(),
        "status": "pass" if not failed_cases else "fail",
        "scorer_fingerprint": corpus_scorer_fingerprint(),
        "datasets": dataset_entries,
        "case_count": len(cases),
        "passed_cases": len(cases) - len(failed_cases),
        "coverage": 1.0,
        "overall_score": round(sum(metric_scores.values()) / len(metric_scores), 6),
        "metric_scores": metric_scores,
        "operational": operational,
        "cases": cases,
        "blockers": [f"case failed: {case_id}" for case_id in failed_cases],
    }


def compare_corpus_to_baseline(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    """Apply non-compensating regression and critical-metric gates."""

    require_fields(
        baseline,
        (
            "schema_version",
            "kind",
            "scorer_fingerprint",
            "datasets",
            "case_ids",
            "overall_score",
            "coverage",
            "metric_scores",
        ),
        label="corpus baseline",
    )
    if baseline.get("kind") != "production_corpus_baseline":
        raise EvaluationInputError("baseline.kind must be production_corpus_baseline")
    require_fields(
        candidate,
        (
            "kind",
            "scorer_fingerprint",
            "datasets",
            "cases",
            "overall_score",
            "coverage",
            "metric_scores",
            "status",
        ),
        label="corpus candidate",
    )
    if candidate.get("kind") != "production_corpus_report":
        raise EvaluationInputError("candidate.kind must be production_corpus_report")
    if baseline["scorer_fingerprint"] != candidate["scorer_fingerprint"]:
        raise EvaluationInputError("candidate uses an incompatible corpus scorer contract")
    if baseline["datasets"] != candidate["datasets"]:
        raise EvaluationInputError("candidate uses incompatible corpus dataset fingerprints")
    candidate_ids = [str(case.get("case_id")) for case in candidate["cases"] if isinstance(case, dict)]
    if baseline["case_ids"] != candidate_ids:
        raise EvaluationInputError("candidate does not contain the frozen baseline case ids in order")
    minimum_overall = _bounded_ratio(
        thresholds.get("minimum_overall_score", 0.8), "minimum_overall_score"
    )
    minimum_coverage = _bounded_ratio(
        thresholds.get("minimum_coverage", 0.85), "minimum_coverage"
    )
    maximum_regression = _bounded_ratio(
        thresholds.get("maximum_regression", 0.02), "maximum_regression"
    )
    critical = thresholds.get("critical_metrics", {})
    if not isinstance(critical, dict):
        raise EvaluationInputError("thresholds.critical_metrics must be an object")
    blockers: list[str] = []
    overall = _bounded_ratio(candidate["overall_score"], "candidate.overall_score")
    coverage = _bounded_ratio(candidate["coverage"], "candidate.coverage")
    baseline_overall = _bounded_ratio(baseline["overall_score"], "baseline.overall_score")
    overall_delta = overall - baseline_overall
    if candidate["status"] != "pass":
        blockers.append("candidate corpus contains failing cases")
    if overall < minimum_overall:
        blockers.append(f"overall score {overall:.3f} is below {minimum_overall:.3f}")
    if coverage < minimum_coverage:
        blockers.append(f"coverage {coverage:.3f} is below {minimum_coverage:.3f}")
    if overall_delta < -maximum_regression:
        blockers.append(f"overall score regressed by {overall_delta:.3f}")
    metric_results: list[dict[str, Any]] = []
    candidate_metrics = candidate["metric_scores"]
    baseline_metrics = baseline["metric_scores"]
    for name in CORPUS_METRICS:
        before = baseline_metrics.get(name)
        after = candidate_metrics.get(name)
        metric_blockers: list[str] = []
        if (
            not isinstance(before, (int, float))
            or isinstance(before, bool)
            or not isfinite(float(before))
            or not 0 <= float(before) <= 1
            or not isinstance(after, (int, float))
            or isinstance(after, bool)
            or not isfinite(float(after))
            or not 0 <= float(after) <= 1
        ):
            metric_blockers.append("metric evidence is missing or invalid")
        else:
            delta = float(after) - float(before)
            if delta < -maximum_regression:
                metric_blockers.append(f"metric regressed by {delta:.3f}")
            required = critical.get(name)
            required_score = (
                _bounded_ratio(critical[name], f"critical_metrics.{name}")
                if name in critical
                else None
            )
            if required_score is not None and float(after) < required_score:
                metric_blockers.append(
                    f"critical metric score {float(after):.3f} is below {required_score:.3f}"
                )
        metric_results.append(
            {
                "name": name,
                "baseline": before,
                "candidate": after,
                "status": "pass" if not metric_blockers else "regression",
                "blockers": metric_blockers,
            }
        )
        blockers.extend(f"{name}: {message}" for message in metric_blockers)
    return {
        "schema_version": 1,
        "kind": "evaluation_regression_gate",
        "created_at": utc_now(),
        "status": "pass" if not blockers else "regression",
        "baseline_fingerprint": stable_fingerprint(baseline),
        "candidate_fingerprint": stable_fingerprint(candidate),
        "case_count": len(candidate_ids),
        "overall_delta": round(overall_delta, 6),
        "coverage": coverage,
        "metrics": metric_results,
        "blockers": blockers,
    }
