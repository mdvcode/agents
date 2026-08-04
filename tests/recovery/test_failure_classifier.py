from __future__ import annotations

import sqlite3
import subprocess

from ai_harness.recovery.classifier import classify_failure


def test_codex_timeout_is_transient_and_retryable() -> None:
    failure = classify_failure(
        subprocess.TimeoutExpired(["codex"], 30),
        124,
        {"run_id": "run-1", "task_id": "task-1", "current_role": "implementation-agent"},
    )
    assert failure.kind == "transient"
    assert failure.retryable is True
    assert failure.role == "implementation-agent"


def test_nonzero_runtime_exit_is_runtime_failure() -> None:
    failure = classify_failure(
        None,
        7,
        {"run_id": "run-1", "task_id": "task-1"},
    )
    assert failure.kind == "runtime_failure"


def test_sqlite_lock_uses_transient_backoff_class() -> None:
    failure = classify_failure(
        sqlite3.OperationalError("database is locked"),
        None,
        {"run_id": "run-1", "task_id": "task-1"},
    )
    assert failure.kind == "transient"
    assert failure.error_type == "SQLiteBusy"


def test_key_error_is_internal_and_unknown_text_is_preserved() -> None:
    failure = classify_failure(
        KeyError("missing recovery field"),
        None,
        {"run_id": "run-1", "task_id": "task-1"},
    )
    assert failure.kind == "internal_error"
    assert "missing recovery field" in failure.message
