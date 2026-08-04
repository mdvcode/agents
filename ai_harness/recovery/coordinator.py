"""Authoritative recovery decision router."""

from __future__ import annotations

import time
from typing import Any

from .backoff import backoff_seconds
from .models import FailureRecord, RecoveryDecision
from .policy import RecoveryPolicy


class RecoveryCoordinator:
    def decide(
        self,
        failure: FailureRecord,
        state: dict[str, Any],
        policy: RecoveryPolicy,
    ) -> RecoveryDecision:
        recovery = state.get("recovery", {})
        if not isinstance(recovery, dict):
            recovery = {}
        total = int(recovery.get("attempts", 0) or 0) + 1
        consecutive = int(recovery.get("consecutive_failures", 0) or 0) + 1
        elapsed = int(recovery.get("elapsed_seconds", 0) or 0)
        started_at = recovery.get("started_at")
        if isinstance(started_at, (int, float)) and not isinstance(started_at, bool):
            elapsed = max(elapsed, int(max(0, time.time() - float(started_at))))
        if (
            total > policy.max_total_recovery_attempts
            or consecutive > policy.max_consecutive_failures
            or elapsed > policy.max_recovery_duration_seconds
        ):
            return RecoveryDecision("dead_letter", 0, "dead_letter", "task recovery budget exhausted", True)

        configured = policy.for_kind(failure.kind)
        if failure.attempt > configured.max_attempts:
            if configured.action == "retry_then_approval":
                return RecoveryDecision("approval", 0, "awaiting_approval", "retry budget exhausted", True)
            if configured.action == "retry_then_resume":
                resumes = int(recovery.get("resume_attempts", 0) or 0)
                if resumes < policy.max_resume_attempts:
                    return RecoveryDecision("resume", 0, "resuming", "retry budget exhausted; resuming checkpoint")
            return RecoveryDecision("dead_letter", 0, "dead_letter", "failure-class recovery budget exhausted", True)
        delay = backoff_seconds(configured.backoff_seconds, failure.attempt)
        action = configured.action
        if action in {"retry", "retry_then_resume", "retry_then_approval"}:
            return RecoveryDecision("retry", delay, "retry_wait", f"bounded retry for {failure.kind}")
        if action in {"output_repair", "existing_repair_loop"}:
            return RecoveryDecision("repair", 0, "repairing", f"bounded repair for {failure.kind}")
        if action == "approval":
            return RecoveryDecision("approval", 0, "awaiting_approval", f"human approval required for {failure.kind}", True)
        if action == "resume_then_dead_letter":
            resumes = int(recovery.get("resume_attempts", 0) or 0)
            if resumes < policy.max_resume_attempts:
                return RecoveryDecision("resume", 0, "resuming", f"resume after {failure.kind}")
            return RecoveryDecision("dead_letter", 0, "dead_letter", "resume budget exhausted", True)
        if action == "fail":
            return RecoveryDecision("fail", 0, "failed", f"unrecoverable {failure.kind}", True)
        return RecoveryDecision("dead_letter", 0, "dead_letter", f"terminal {failure.kind}", True)
