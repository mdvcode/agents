"""Bounded recovery backoff helpers."""

from __future__ import annotations


def backoff_seconds(schedule: tuple[int, ...] | list[int], attempt: int) -> int:
    if not schedule:
        return 0
    index = min(max(attempt - 1, 0), len(schedule) - 1)
    return max(0, int(schedule[index]))
