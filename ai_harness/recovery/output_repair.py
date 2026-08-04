"""Bounded validation-only repair for structured model output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class OutputRepairResult:
    output: str
    errors: tuple[str, ...]
    attempts: int
    repaired: bool


def repair_output(
    *,
    original_output: str,
    schema: dict[str, Any],
    validation_errors: list[str],
    invoke: Callable[[str], str],
    validate: Callable[[str], list[str]],
    max_attempts: int = 2,
) -> OutputRepairResult:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    output = original_output
    errors = list(validation_errors)
    for attempt in range(1, max_attempts + 1):
        prompt = "\n".join(
            [
                "Original structured output:",
                output,
                "Schema:",
                __import__("json").dumps(schema, ensure_ascii=False, sort_keys=True),
                "Validation errors:",
                __import__("json").dumps(errors, ensure_ascii=False),
            ]
        )
        output = invoke(prompt)
        errors = validate(output)
        if not errors:
            return OutputRepairResult(output, (), attempt, True)
    return OutputRepairResult(output, tuple(errors), max_attempts, False)
