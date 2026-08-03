#!/usr/bin/env python3
"""Run a versioned evaluation dataset against explicitly mapped Harness runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.evaluation.io import EvaluationInputError, read_object, write_object
from ai_harness.evaluation.runner import evaluate_dataset


def _subject(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name.strip() or not path.strip():
        raise argparse.ArgumentTypeError("subject must use NAME=/path/to/run")
    return name.strip(), Path(path).expanduser()


def _variant(value: str) -> tuple[str, Path]:
    name, path = _subject(value)
    return name, path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=Path("evals/rubrics/harness_run_v1.json"))
    parser.add_argument("--subject", type=_subject, action="append", required=True)
    parser.add_argument("--variant", type=_variant, action="append", default=[], help="Optional NAME=variant.json metadata mapping.")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    subjects: dict[str, Path] = {}
    for name, path in args.subject:
        if name in subjects:
            print(json.dumps({"status": "error", "error": f"duplicate subject: {name}"}))
            return 2
        subjects[name] = path
    try:
        variants: dict[str, dict[str, object]] = {}
        for name, path in args.variant:
            if name in variants:
                raise EvaluationInputError(f"duplicate variant mapping: {name}")
            value = read_object(path)
            assert value is not None
            variants[name] = value
        dataset = read_object(args.dataset)
        rubric = read_object(args.rubric)
        assert dataset is not None and rubric is not None
        result = evaluate_dataset(dataset, rubric, subjects, variants)
    except EvaluationInputError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    write_object(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
