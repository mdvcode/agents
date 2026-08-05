"""Load and validate the Harness-owned recovery policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import FAILURE_KINDS


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / ".agent-recovery.yaml"


@dataclass(frozen=True)
class FailureClassPolicy:
    action: str
    max_attempts: int
    backoff_seconds: tuple[int, ...] = ()


@dataclass(frozen=True)
class RuntimeLimits:
    role_timeout_seconds: int
    workflow_timeout_seconds: int
    idle_timeout_seconds: int
    tool_timeout_seconds: int
    approval_timeout_seconds: int
    shutdown_grace_seconds: int
    max_output_bytes: int
    max_artifact_bytes: int
    max_concurrent_subprocesses: int
    max_open_files: int


@dataclass(frozen=True)
class RecoveryPolicy:
    max_total_recovery_attempts: int
    max_resume_attempts: int
    max_consecutive_failures: int
    max_recovery_duration_seconds: int
    failure_classes: dict[str, FailureClassPolicy]
    runtime_limits: RuntimeLimits

    def for_kind(self, kind: str) -> FailureClassPolicy:
        try:
            return self.failure_classes[kind]
        except KeyError as exc:
            raise ValueError(f"recovery policy does not define {kind!r}") from exc


def _positive(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def load_recovery_policy(path: Path = DEFAULT_POLICY) -> RecoveryPolicy:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(".agent-recovery.yaml must contain version: 1")
    task = data.get("task_recovery")
    classes = data.get("failure_classes")
    if not isinstance(task, dict) or not isinstance(classes, dict):
        raise ValueError("recovery policy requires task_recovery and failure_classes objects")
    raw_limits = data.get("runtime_limits", {})
    if not isinstance(raw_limits, dict):
        raise ValueError("runtime_limits must be an object")
    missing = sorted(FAILURE_KINDS - set(classes))
    if missing:
        raise ValueError("recovery policy is missing failure classes: " + ", ".join(missing))
    parsed: dict[str, FailureClassPolicy] = {}
    allowed_actions = {
        "retry",
        "retry_then_resume",
        "output_repair",
        "retry_then_approval",
        "existing_repair_loop",
        "approval",
        "resume_then_dead_letter",
        "dead_letter",
        "fail",
    }
    for kind, raw in classes.items():
        if kind not in FAILURE_KINDS or not isinstance(raw, dict):
            raise ValueError(f"invalid recovery failure class: {kind!r}")
        action = raw.get("action")
        if action not in allowed_actions:
            raise ValueError(f"invalid recovery action for {kind}: {action!r}")
        backoff = raw.get("backoff_seconds", [])
        if not isinstance(backoff, list) or any(not isinstance(item, int) or item < 0 for item in backoff):
            raise ValueError(f"{kind}.backoff_seconds must contain non-negative integers")
        parsed[kind] = FailureClassPolicy(
            action=str(action),
            max_attempts=_positive(raw.get("max_attempts", 1), f"{kind}.max_attempts"),
            backoff_seconds=tuple(backoff),
        )
    return RecoveryPolicy(
        max_total_recovery_attempts=_positive(task.get("max_total_recovery_attempts"), "max_total_recovery_attempts"),
        max_resume_attempts=_positive(task.get("max_resume_attempts"), "max_resume_attempts"),
        max_consecutive_failures=_positive(task.get("max_consecutive_failures"), "max_consecutive_failures"),
        max_recovery_duration_seconds=_positive(task.get("max_recovery_duration_seconds"), "max_recovery_duration_seconds"),
        failure_classes=parsed,
        runtime_limits=RuntimeLimits(
            role_timeout_seconds=_positive(raw_limits.get("role_timeout_seconds", 600), "role_timeout_seconds"),
            workflow_timeout_seconds=_positive(raw_limits.get("workflow_timeout_seconds", 14400), "workflow_timeout_seconds"),
            idle_timeout_seconds=_positive(raw_limits.get("idle_timeout_seconds", 900), "idle_timeout_seconds"),
            tool_timeout_seconds=_positive(raw_limits.get("tool_timeout_seconds", 120), "tool_timeout_seconds"),
            approval_timeout_seconds=_positive(raw_limits.get("approval_timeout_seconds", 86400), "approval_timeout_seconds"),
            shutdown_grace_seconds=_positive(raw_limits.get("shutdown_grace_seconds", 10), "shutdown_grace_seconds"),
            max_output_bytes=_positive(raw_limits.get("max_output_bytes", 10 * 1024 * 1024), "max_output_bytes"),
            max_artifact_bytes=_positive(raw_limits.get("max_artifact_bytes", 50 * 1024 * 1024), "max_artifact_bytes"),
            max_concurrent_subprocesses=_positive(
                raw_limits.get("max_concurrent_subprocesses", 32), "max_concurrent_subprocesses"
            ),
            max_open_files=_positive(raw_limits.get("max_open_files", 256), "max_open_files"),
        ),
    )
