from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ai_harness.evaluation.corpus import (
    CORPUS_METRICS,
    compare_corpus_to_baseline,
    evaluate_corpus,
    validate_corpus_dataset,
)
from ai_harness.evaluation.io import EvaluationInputError


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATHS = [
    ROOT / "evals" / "datasets" / name
    for name in (
        "core_engineering_v1.json",
        "security_routing_v1.json",
        "context_retrieval_v1.json",
        "repair_loops_v1.json",
        "publication_safety_v1.json",
        "human_approval_v1.json",
    )
]
BASELINE = json.loads(
    (ROOT / "evals" / "baselines" / "production_e2_v1.json").read_text(encoding="utf-8")
)
EXPERIMENT = json.loads(
    (ROOT / "evals" / "experiments" / "production_e2_v1.json").read_text(encoding="utf-8")
)


def load_datasets() -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in DATASET_PATHS]


def find_case(datasets: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    for dataset in datasets:
        for case in dataset["cases"]:
            if case["case_id"] == case_id:
                return case
    raise AssertionError(f"missing case {case_id}")


def mutate_case(case: dict[str, Any], mutation: dict[str, Any]) -> None:
    parts = str(mutation["field"]).split(".")
    target: Any = case
    for part in parts[:-1]:
        target = target[part]
    field = parts[-1]
    if "add_value" in mutation:
        target[field].append(mutation["add_value"])
    elif "remove_value" in mutation:
        target[field].remove(mutation["remove_value"])
    else:
        target[field] = mutation["replace_value"]


def test_production_corpus_has_required_volume_and_clean_frozen_baseline() -> None:
    datasets = load_datasets()
    report = evaluate_corpus(datasets)
    negative_count = sum(
        "negative" in case["tags"]
        for dataset in datasets
        for case in dataset["cases"]
    )

    assert report["status"] == "pass"
    assert report["case_count"] == 30
    assert report["passed_cases"] == 30
    assert negative_count >= 10
    assert report["coverage"] == 1.0
    assert report["overall_score"] == 1.0
    assert tuple(report["metric_scores"]) == CORPUS_METRICS
    assert report["operational"]["latency_ms"]["status"] == "unavailable"
    assert report["operational"]["tokens"]["status"] == "unavailable"
    assert report["operational"]["cost_usd"] == {
        "status": "unknown",
        "samples": 0,
        "total": None,
    }
    assert report["operational"]["human_interventions"]["total"] > 0
    assert compare_corpus_to_baseline(BASELINE, report, EXPERIMENT["thresholds"])["status"] == "pass"


def test_every_reviewed_regression_mutation_has_an_unambiguous_failure() -> None:
    regressions = json.loads(
        (ROOT / "evals" / "regressions" / "production_regressions_v1.json").read_text(
            encoding="utf-8"
        )
    )["regressions"]
    assert len(regressions) >= 10
    for regression in regressions:
        datasets = copy.deepcopy(load_datasets())
        case = find_case(datasets, regression["source_case_id"])
        mutate_case(case, regression["mutation"])

        report = evaluate_corpus(datasets)
        result_case = next(
            item for item in report["cases"] if item["case_id"] == regression["source_case_id"]
        )
        metric = next(
            item for item in result_case["metrics"] if item["name"] == regression["metric"]
        )

        assert report["status"] == "fail", regression["regression_id"]
        assert metric["status"] == "fail", regression["regression_id"]
        assert any(regression["expected_failure"] in failure for failure in metric["failures"])


def test_critical_metric_cannot_be_compensated_by_high_aggregate_score() -> None:
    datasets = load_datasets()
    case = find_case(datasets, "security-critical-sensitive-value-001")
    case["subject"]["findings"].remove("sensitive_value")
    candidate = evaluate_corpus(datasets)

    gate = compare_corpus_to_baseline(BASELINE, candidate, EXPERIMENT["thresholds"])

    assert candidate["overall_score"] > 0.99
    assert gate["status"] == "regression"
    assert any("security: critical metric" in blocker for blocker in gate["blockers"])


def test_gate_rejects_incompatible_dataset_fingerprint() -> None:
    candidate = evaluate_corpus(load_datasets())
    candidate["datasets"][0]["fingerprint"] = "different"

    with pytest.raises(EvaluationInputError, match="incompatible corpus dataset"):
        compare_corpus_to_baseline(BASELINE, candidate, EXPERIMENT["thresholds"])


def test_gate_rejects_incompatible_scorer_contract() -> None:
    candidate = evaluate_corpus(load_datasets())
    candidate["scorer_fingerprint"] = "different"

    with pytest.raises(EvaluationInputError, match="incompatible corpus scorer"):
        compare_corpus_to_baseline(BASELINE, candidate, EXPERIMENT["thresholds"])


def test_gate_rejects_non_finite_scores() -> None:
    candidate = evaluate_corpus(load_datasets())
    candidate["overall_score"] = float("nan")

    with pytest.raises(EvaluationInputError, match="finite number"):
        compare_corpus_to_baseline(BASELINE, candidate, EXPERIMENT["thresholds"])


def test_corpus_rejects_executable_fields() -> None:
    dataset = copy.deepcopy(load_datasets()[0])
    dataset["cases"][0]["shell"] = "do-not-run"

    with pytest.raises(EvaluationInputError, match="forbidden executable field"):
        validate_corpus_dataset(dataset)


@pytest.mark.parametrize(
    ("field", "value"),
    (("latency_ms", -1), ("tokens", -1), ("tokens", 1.5), ("cost_usd", -0.01)),
)
def test_corpus_rejects_invalid_operational_evidence(field: str, value: object) -> None:
    dataset = copy.deepcopy(load_datasets()[0])
    dataset["cases"][0]["subject"][field] = value

    with pytest.raises(EvaluationInputError, match=field):
        validate_corpus_dataset(dataset)


def test_eval_regression_cli_is_read_only_and_returns_nonzero_for_regression(
    tmp_path: Path,
) -> None:
    datasets = load_datasets()
    case = find_case(datasets, "publication-high-denied-003")
    case["subject"]["actions"].append("commit")
    candidate = evaluate_corpus(datasets)
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / "gate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [*DATASET_PATHS, ROOT / "evals" / "baselines" / "production_e2_v1.json"]
    }

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/eval_regression.py",
            "--candidate",
            str(candidate_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "regression"
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
