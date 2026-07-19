#!/usr/bin/env python3
"""Preflight the configured runtime through the provider-neutral runtime boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtimes import RuntimeConfigurationError, create_runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--runtime-provider", default="")
    parser.add_argument("--timeout-seconds", type=int, default=45)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        runtime = create_runtime(provider=args.runtime_provider)
    except RuntimeConfigurationError as exc:
        payload = {
            "execution_status": "blocked",
            "blockers": [str(exc)],
            "warnings": [],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1
    payload = runtime.preflight(
        worktree=args.repo.resolve(),
        timeout_seconds=args.timeout_seconds,
    )
    payload["runtime"] = runtime.descriptor.as_json()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("execution_status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
