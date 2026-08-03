#!/usr/bin/env python3
"""Build a coverage-aware leaderboard from scorecard or evaluation reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.evaluation.io import EvaluationInputError, read_object, write_object
from ai_harness.evaluation.leaderboard import build_leaderboard, render_markdown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--minimum-coverage", type=float, default=0.6)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        reports = []
        for path in args.reports:
            report = read_object(path)
            assert report is not None
            reports.append((str(path), report))
        result = build_leaderboard(reports, minimum_coverage=args.minimum_coverage)
    except EvaluationInputError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    write_object(args.output, result)
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
