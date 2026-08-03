#!/usr/bin/env python3
"""Run the frozen production corpus and fail on incompatible or critical regressions."""

from __future__ import annotations

import argparse
import json
import sys
from math import isfinite
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.evaluation.corpus import (
    CORPUS_METRICS,
    compare_corpus_to_baseline,
    evaluate_corpus,
)
from ai_harness.evaluation.io import EvaluationInputError, read_object, write_object


DEFAULT_MANIFEST = ROOT / "evals" / "experiments" / "production_e2_v1.json"


def repository_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise EvaluationInputError(f"{label} must be a repository-relative path")
    candidate = (ROOT / value).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise EvaluationInputError(f"{label} resolves outside the repository") from exc
    if not candidate.is_file():
        raise EvaluationInputError(f"{label} does not exist: {value}")
    return candidate


def validate_manifest(manifest: dict[str, Any]) -> None:
    required = ("schema_version", "kind", "experiment_id", "baseline", "candidate", "datasets", "thresholds")
    missing = [field for field in required if field not in manifest]
    if missing:
        raise EvaluationInputError("experiment manifest is missing: " + ", ".join(missing))
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "evaluation_experiment":
        raise EvaluationInputError("experiment manifest version or kind is invalid")
    if not isinstance(manifest.get("datasets"), list) or not manifest["datasets"]:
        raise EvaluationInputError("experiment manifest datasets must be non-empty")
    baseline = manifest.get("baseline")
    candidate = manifest.get("candidate")
    thresholds = manifest.get("thresholds")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict) or not isinstance(thresholds, dict):
        raise EvaluationInputError("experiment baseline, candidate, and thresholds must be objects")
    if candidate.get("generator") != "production_corpus_v1":
        raise EvaluationInputError("only the deterministic production_corpus_v1 generator is allowed")
    for field in ("minimum_overall_score", "minimum_coverage", "maximum_regression"):
        value = thresholds.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise EvaluationInputError(f"experiment thresholds.{field} must be between 0 and 1")
    critical = thresholds.get("critical_metrics")
    if not isinstance(critical, dict) or not critical:
        raise EvaluationInputError("experiment critical_metrics must be a non-empty object")
    unknown = sorted(set(critical) - set(CORPUS_METRICS))
    if unknown:
        raise EvaluationInputError("experiment has unknown critical metrics: " + ", ".join(unknown))
    for name, threshold in critical.items():
        if (
            not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not isfinite(float(threshold))
            or not 0 <= float(threshold) <= 1
        ):
            raise EvaluationInputError(f"critical metric {name!r} threshold must be between 0 and 1")


def run_gate(
    manifest_path: Path,
    *,
    candidate_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_object(manifest_path)
    assert manifest is not None
    validate_manifest(manifest)
    baseline_config = manifest["baseline"]
    baseline = read_object(repository_path(baseline_config.get("path"), label="baseline.path"))
    assert baseline is not None
    datasets = []
    for index, value in enumerate(manifest["datasets"]):
        dataset = read_object(repository_path(value, label=f"datasets[{index}]"))
        assert dataset is not None
        datasets.append(dataset)
    if candidate_path is None:
        candidate = evaluate_corpus(datasets)
    else:
        candidate = read_object(candidate_path)
        assert candidate is not None
    gate = compare_corpus_to_baseline(baseline, candidate, manifest["thresholds"])
    gate["experiment_id"] = str(manifest["experiment_id"])
    return candidate, gate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--candidate", type=Path, default=None)
    parser.add_argument("--candidate-output", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        candidate, gate = run_gate(args.manifest.resolve(), candidate_path=args.candidate)
    except EvaluationInputError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.candidate_output is not None:
        write_object(args.candidate_output, candidate)
    if args.output is not None:
        write_object(args.output, gate)
    print(json.dumps(gate, indent=2, ensure_ascii=False))
    return 0 if gate["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
