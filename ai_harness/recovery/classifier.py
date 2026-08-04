"""Deterministic failure classification; no model output controls policy."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .models import FailureRecord


def _text(exception: BaseException | None, role_result: dict[str, Any] | None) -> str:
    parts = [str(exception or "")]
    if isinstance(role_result, dict):
        parts.extend(
            [
                str(role_result.get("summary", "")),
                " ".join(str(item) for item in role_result.get("blockers", []) if isinstance(item, str)),
            ]
        )
    return " ".join(parts).lower()


def _kind(
    exception: BaseException | None,
    process_returncode: int | None,
    workflow_state: dict[str, Any],
    role_result: dict[str, Any] | None,
) -> tuple[str, str]:
    failure_hint = role_result.get("_failure", {}) if isinstance(role_result, dict) else {}
    if isinstance(failure_hint, dict) and failure_hint.get("kind") in {
        "transient",
        "runtime_failure",
        "invalid_output",
        "tool_failure",
        "verification_failure",
        "policy_block",
        "human_input_required",
        "internal_error",
        "unrecoverable",
    }:
        return str(failure_hint["kind"]), str(failure_hint.get("error_type", "RuntimeFailure"))
    text = _text(exception, role_result)
    if isinstance(exception, (subprocess.TimeoutExpired, TimeoutError)) or process_returncode == 124:
        return "transient", "TimeoutExpired"
    if isinstance(exception, sqlite3.OperationalError) and ("locked" in text or "busy" in text):
        return "transient", "SQLiteBusy"
    if any(value in text for value in ("rate limit", "temporarily unavailable", "connection reset", "timed out")):
        return "transient", type(exception).__name__ if exception else "TransientFailure"
    if isinstance(exception, (KeyError, TypeError, AssertionError)):
        return "internal_error", type(exception).__name__
    if any(value in text for value in ("malformed json", "schema validation", "missing required field", "without required artifacts")):
        return "invalid_output", "InvalidStructuredOutput"
    if any(value in text for value in ("pytest", "quality check", "verification failed")):
        return "verification_failure", "VerificationFailure"
    if workflow_state.get("execution_status") == "awaiting_approval":
        return "human_input_required", "ApprovalRequired"
    if any(value in text for value in ("protected path", "policy denied", "policy block")):
        return "policy_block", "PolicyBlock"
    if process_returncode not in (None, 0):
        return "runtime_failure", "RuntimeProcessFailure"
    if exception is not None:
        return "internal_error", type(exception).__name__
    return "unrecoverable", "UnknownFailure"


def classify_failure(
    exception: BaseException | None = None,
    process_returncode: int | None = None,
    workflow_state: dict[str, Any] | None = None,
    role_result: dict[str, Any] | None = None,
    artifacts: Path | None = None,
    *,
    run_id: str = "",
    task_id: str = "",
    role: str = "",
    stage: str = "runtime_execute",
    checkpoint: str = "before_runtime_execute",
    attempt: int = 1,
    max_attempts: int = 1,
) -> FailureRecord:
    state = workflow_state or {}
    kind, error_type = _kind(exception, process_returncode, state, role_result)
    hint = role_result.get("_failure", {}) if isinstance(role_result, dict) else {}
    hint_message = hint.get("message", "") if isinstance(hint, dict) else ""
    message = hint_message or exception or (
        role_result.get("summary", "") if isinstance(role_result, dict) else f"process exit {process_returncode}"
    )
    metadata: dict[str, Any] = {"process_returncode": process_returncode}
    if artifacts is not None:
        metadata["artifacts_dir"] = str(artifacts.resolve())
    return FailureRecord.create(
        run_id=run_id or str(state.get("run_id", "unknown-run")),
        task_id=task_id or str(state.get("task_id", "unknown-task")),
        role=role or str(state.get("current_role", "")),
        stage=stage,
        kind=kind,
        error_type=error_type,
        message=message or error_type,
        retryable=kind in {"transient", "runtime_failure", "tool_failure"},
        repairable=kind in {"invalid_output", "verification_failure"},
        attempt=attempt,
        max_attempts=max_attempts,
        checkpoint=checkpoint,
        metadata=metadata,
    )
