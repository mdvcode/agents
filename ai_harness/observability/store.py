"""Bounded readers for run-scoped OpenTelemetry JSONL evidence."""

from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path
from typing import Any


MAX_FILES = 100
MAX_BYTES_PER_FILE = 1_048_576
MAX_SPANS = 200


def _tail_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        data = handle.read(limit)
    if size > limit:
        _, _, data = data.partition(b"\n")
    return data


def recent_spans(runs_dir: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(limit, MAX_SPANS))
    paths = sorted(
        (
            path
            for path in runs_dir.glob("*/raw-events/otel-spans.jsonl")
            if path.is_file() and not path.is_symlink()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:MAX_FILES]
    records: deque[dict[str, Any]] = deque(maxlen=bounded_limit)
    for path in reversed(paths):
        try:
            payload = _tail_bytes(path, MAX_BYTES_PER_FILE).decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in payload.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            value = dict(value)
            value["run_id"] = path.parents[1].name
            records.append(value)
    return sorted(records, key=lambda item: int(item.get("end_time_unix_nano", 0)), reverse=True)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return round(ordered[index], 3)


def trace_summary(runs_dir: Path, *, limit: int = 50) -> dict[str, Any]:
    spans = recent_spans(runs_dir, limit=limit)
    durations = [float(item.get("duration_ms", 0) or 0) for item in spans]
    return {
        "count": len(spans),
        "status_counts": dict(Counter(str(item.get("status", "unset")) for item in spans)),
        "duration_ms": {
            "p50": percentile(durations, 0.50),
            "p95": percentile(durations, 0.95),
        },
        "items": spans,
    }
