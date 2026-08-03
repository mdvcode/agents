#!/usr/bin/env python3
"""Compare compatible baseline and candidate evaluation reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.evaluation.comparison import compare_reports
from ai_harness.evaluation.io import EvaluationInputError, read_object, write_object


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--regression-tolerance", type=float, default=0.02)
    parser.add_argument("--minimum-coverage", type=float, default=0.6)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        baseline = read_object(args.baseline)
        candidate = read_object(args.candidate)
        assert baseline is not None and candidate is not None
        result = compare_reports(
            baseline,
            candidate,
            regression_tolerance=args.regression_tolerance,
            minimum_coverage=args.minimum_coverage,
        )
    except EvaluationInputError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    write_object(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
