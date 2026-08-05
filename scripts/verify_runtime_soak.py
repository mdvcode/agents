#!/usr/bin/env python3
"""Verify a real 30-task, multi-hour production runtime soak report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.recovery.soak import validate_soak_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--minimum-duration-seconds", type=int, default=7200)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        value = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2
    errors = validate_soak_report(value, minimum_duration_seconds=args.minimum_duration_seconds)
    print(
        json.dumps(
            {"status": "passed" if not errors else "failed", "errors": errors},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
