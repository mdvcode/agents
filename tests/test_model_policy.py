from __future__ import annotations

import pytest

from ai_harness.model_policy import (
    ModelPolicyError,
    select_execution_profile,
    validate_request_profile,
)


PROFILES = {
    "complex": {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "service_tier": "fast",
    },
    "balanced": {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "service_tier": "fast",
    },
    "economy": {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "service_tier": "fast",
    },
}


def select(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "role": "implementation-agent",
        "goal": "Update local status output",
        "risk_class": "low",
        "changed_files": ["ai_harness/cli.py"],
        "changed_lines": 20,
        "changed_areas": {"code"},
        "profiles": PROFILES,
    }
    values.update(overrides)
    return select_execution_profile(**values)  # type: ignore[arg-type]


def test_ordinary_local_implementation_uses_terra_medium() -> None:
    selected = select()

    assert selected["execution_profile"] == "balanced"
    assert selected["model"] == "gpt-5.6-terra"
    assert selected["reasoning_effort"] == "medium"


def test_complex_implementation_and_risk_bearing_review_use_sol_high() -> None:
    implementation = select(risk_class="high")
    review = select(role="reviewer", risk_class="medium")

    assert implementation["execution_profile"] == "complex"
    assert review["execution_profile"] == "complex"
    assert implementation["reasoning_effort"] == "high"


def test_mechanical_work_and_first_simple_repair_use_luna_low() -> None:
    mechanical = select(goal="Format imports")
    repair = select(repair_iteration=1)
    ci_repair = select(role="ci-repair-agent", repair_iteration=1)

    assert mechanical["execution_profile"] == "economy"
    assert repair["execution_profile"] == "economy"
    assert ci_repair["execution_profile"] == "economy"
    assert repair["model"] == "gpt-5.6-luna"


def test_only_failure_or_repeated_repair_escalates_to_sol() -> None:
    continued_after_answer = select(prior_failure=False)
    failed_attempt = select(prior_failure=True)
    repeated_repair = select(repair_iteration=2)

    assert continued_after_answer["execution_profile"] == "balanced"
    assert failed_attempt["execution_profile"] == "complex"
    assert repeated_repair["execution_profile"] == "complex"
    assert failed_attempt["escalation_level"] == 1


def test_request_cannot_override_configured_profile() -> None:
    with pytest.raises(ModelPolicyError, match="requires model"):
        validate_request_profile(
            {"execution_profile": "economy", "model": "gpt-5.6-sol"},
            profiles=PROFILES,
        )
