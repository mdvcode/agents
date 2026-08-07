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


def test_runtime_resource_limits_are_authoritative_and_bounded() -> None:
    limits = load_recovery_policy(ROOT / ".agent-recovery.yaml").runtime_limits

    assert limits.role_timeout_seconds > 0
    assert limits.workflow_timeout_seconds >= limits.role_timeout_seconds
    assert limits.idle_timeout_seconds > 0
    assert limits.shutdown_grace_seconds > 0
    assert limits.max_output_bytes > 0
    assert limits.max_artifact_bytes >= limits.max_output_bytes
    assert 1 <= limits.max_concurrent_subprocesses <= 32
    assert limits.max_open_files >= 4


def test_default_policy_resolves_from_installed_harness_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected_timeout = load_recovery_policy(
        ROOT / ".agent-recovery.yaml"
    ).runtime_limits.role_timeout_seconds
    harness = tmp_path / "share" / "ai-harness"
    (harness / "scripts").mkdir(parents=True)
    (harness / "schemas").mkdir()
    (harness / ".agent-runtime.yaml").write_text("version: 1\n", encoding="utf-8")
    (harness / "scripts" / "task_queue.py").write_text("", encoding="utf-8")
    (harness / "schemas" / "task_envelope.schema.json").write_text("{}", encoding="utf-8")
    (harness / ".agent-recovery.yaml").write_text(
        (ROOT / ".agent-recovery.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("AI_HARNESS_HOME", str(harness))

    policy = load_recovery_policy()

    assert policy.runtime_limits.role_timeout_seconds == expected_timeout
