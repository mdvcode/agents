"""Structured recovery records and decisions."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAILURE_KINDS = {
    "transient",
    "runtime_failure",
    "invalid_output",
    "tool_failure",
    "verification_failure",
    "policy_block",
    "human_input_required",
    "internal_error",
    "unrecoverable",
}
RECOVERY_ACTIONS = {"retry", "repair", "resume", "approval", "dead_letter", "fail"}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*)([^\s]+(?:\s+[^\s]+)?)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitized_message(value: object, *, limit: int = 1000) -> str:
    message = str(value).replace("\x00", "")
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]", message)
    return message[:limit]


@dataclass(frozen=True)
class FailureRecord:
    failure_id: str
    run_id: str
    task_id: str
    role: str
    stage: str
    kind: str
    error_type: str
    message: str
    retryable: bool
    repairable: bool
    attempt: int
    max_attempts: int
    checkpoint: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in FAILURE_KINDS:
            raise ValueError(f"unsupported failure kind: {self.kind}")
        if not self.failure_id or not self.run_id or not self.task_id:
            raise ValueError("failure_id, run_id, and task_id are required")
        if self.attempt < 1 or self.max_attempts < 1:
            raise ValueError("failure attempts must be positive")

    def as_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        task_id: str,
        role: str,
        stage: str,
        kind: str,
        error_type: str,
        message: object,
        retryable: bool,
        repairable: bool,
        attempt: int = 1,
        max_attempts: int = 1,
        checkpoint: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "FailureRecord":
        return cls(
            failure_id=f"failure-{uuid.uuid4().hex[:16]}",
            run_id=run_id,
            task_id=task_id,
            role=role,
            stage=stage,
            kind=kind,
            error_type=error_type,
            message=sanitized_message(message),
            retryable=retryable,
            repairable=repairable,
            attempt=attempt,
            max_attempts=max_attempts,
            checkpoint=checkpoint,
            created_at=utc_now(),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    delay_seconds: int
    next_status: str
    reason: str
    requires_human: bool = False

    def __post_init__(self) -> None:
        if self.action not in RECOVERY_ACTIONS:
            raise ValueError(f"unsupported recovery action: {self.action}")
        if self.delay_seconds < 0:
            raise ValueError("recovery delay cannot be negative")

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def persist_failure(run_dir: Path, failure: FailureRecord) -> Path:
    resolved = run_dir.resolve()
    failures_dir = resolved / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    path = failures_dir / f"{failure.failure_id}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(failure.as_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    errors = resolved / "errors.jsonl"
    with errors.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "time": failure.created_at,
                    "stage": failure.stage,
                    "role": failure.role,
                    "code": failure.error_type,
                    "message": failure.message,
                    "failure_id": failure.failure_id,
                    "failure_kind": failure.kind,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return path
