#!/usr/bin/env python3
"""Score one AI Harness run against a versioned evaluation rubric."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.evaluation.io import EvaluationInputError, read_object, write_object
from ai_harness.evaluation.scoring import score_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=Path("evals/rubrics/harness_run_v1.json"))
    parser.add_argument("--label", default=None)
    parser.add_argument("--variant", type=Path, default=None, help="Optional JSON object describing model/prompt/RAG/loop/memory settings.")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rubric = read_object(args.rubric)
        assert rubric is not None
        variant = read_object(args.variant) if args.variant is not None else None
        result = score_run(args.run_dir, rubric, label=args.label, variant=variant)
    except EvaluationInputError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.output is not None:
        write_object(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
