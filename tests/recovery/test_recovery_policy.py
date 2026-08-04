from __future__ import annotations

from pathlib import Path

import pytest

from ai_harness.recovery.coordinator import RecoveryCoordinator
from ai_harness.recovery.models import FailureRecord
from ai_harness.recovery.policy import load_recovery_policy


ROOT = Path(__file__).resolve().parents[2]


def failure(kind: str, attempt: int = 1) -> FailureRecord:
    return FailureRecord.create(
        run_id="run-1",
        task_id="task-1",
        role="implementation-agent",
        stage="runtime_execute",
        kind=kind,
        error_type="InjectedFailure",
        message="injected",
        retryable=kind in {"transient", "runtime_failure", "tool_failure"},
        repairable=kind in {"invalid_output", "verification_failure"},
        attempt=attempt,
        max_attempts=3,
        checkpoint="before_runtime_execute",
    )


def test_policy_routes_retry_repair_and_existing_verification_loop() -> None:
    policy = load_recovery_policy(ROOT / ".agent-recovery.yaml")
    coordinator = RecoveryCoordinator()
    assert coordinator.decide(failure("transient"), {}, policy).action == "retry"
    assert coordinator.decide(failure("invalid_output"), {}, policy).action == "repair"
    assert coordinator.decide(failure("verification_failure"), {}, policy).action == "repair"


def test_recovery_budget_exhaustion_routes_to_dead_letter() -> None:
    policy = load_recovery_policy(ROOT / ".agent-recovery.yaml")
    state = {"recovery": {"attempts": policy.max_total_recovery_attempts}}
    decision = RecoveryCoordinator().decide(failure("transient"), state, policy)
    assert decision.action == "dead_letter"
    assert decision.next_status == "dead_letter"


def test_policy_rejects_unbounded_or_missing_classes(tmp_path: Path) -> None:
    policy = tmp_path / "recovery.yaml"
    policy.write_text("version: 1\ntask_recovery: {}\nfailure_classes: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_recovery_policy(policy)
