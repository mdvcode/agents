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


def test_failure_first_increases_reasoning_before_model_upgrade() -> None:
    continued_after_answer = select(prior_failure=False)
    failed_attempt = select(prior_failure=True)
    repeated_repair = select(repair_iteration=2)

    assert continued_after_answer["execution_profile"] == "balanced"
    assert failed_attempt["execution_profile"] == "balanced"
    assert failed_attempt["reasoning_effort"] == "high"
    assert repeated_repair["execution_profile"] == "balanced"
    assert failed_attempt["escalation_level"] == 1


def test_request_cannot_override_configured_profile() -> None:
    with pytest.raises(ModelPolicyError, match="requires model"):
        validate_request_profile(
            {"execution_profile": "economy", "model": "gpt-5.6-sol"},
            profiles=PROFILES,
        )


def test_failure_aware_escalation_does_not_upgrade_for_pytest_failure() -> None:
    selected = select(
        prior_failure=True,
        failure_type="test_failure",
        previous_profile="economy",
        repair_iteration=1,
    )

    assert selected["execution_profile"] == "economy"
    assert selected["escalation_level"] == 0


def test_reasoning_failure_climbs_one_profile_at_a_time() -> None:
    from_economy = select(
        failure_type="reasoning_failure",
        previous_profile="economy",
    )
    from_balanced = select(
        failure_type="invalid_solution",
        previous_profile="balanced",
    )

    assert from_economy["execution_profile"] == "economy"
    assert from_economy["reasoning_effort"] == "medium"
    assert from_balanced["execution_profile"] == "balanced"
    assert from_balanced["reasoning_effort"] == "high"
    assert from_economy["escalation_level"] == 1


def test_reasoning_ladder_upgrades_model_then_stops_for_human() -> None:
    upgraded = select(
        failure_type="reasoning_failure",
        previous_profile="economy",
        previous_reasoning_effort="medium",
    )
    terminal = select(
        failure_type="repeated_invalid_solution",
        previous_profile="complex",
        previous_reasoning_effort="xhigh",
    )

    assert upgraded["execution_profile"] == "balanced"
    assert upgraded["reasoning_effort"] == "medium"
    assert terminal["terminal_action"] == "human_or_dead_letter"


def test_configured_profile_allows_only_its_reasoning_ladder() -> None:
    selected = validate_request_profile(
        {"execution_profile": "economy", "reasoning_effort": "medium"},
        profiles=PROFILES,
    )
    assert selected["reasoning_effort"] == "medium"
    with pytest.raises(ModelPolicyError, match="does not allow"):
        validate_request_profile(
            {"execution_profile": "economy", "reasoning_effort": "xhigh"},
            profiles=PROFILES,
        )


def test_context_capability_and_budget_pressure_are_selection_inputs() -> None:
    large_context = select(context_size=20_000)
    deep_review = select(required_capability="deep_review")
    constrained = select(budget_pressure=True)

    assert large_context["execution_profile"] == "complex"
    assert deep_review["execution_profile"] == "complex"
    assert constrained["execution_profile"] == "economy"


def test_repeated_deterministic_failure_still_does_not_escalate_model() -> None:
    selected = select(
        failure_type="test_failure",
        previous_profile="economy",
        repair_iteration=3,
        repair_count=3,
    )

    assert selected["execution_profile"] == "economy"
    assert selected["escalation_level"] == 0


def test_adaptive_plan_profile_is_the_initial_model_choice() -> None:
    selected = select(
        planned_profile="economy",
        task_complexity="trivial",
    )

    assert selected["execution_profile"] == "economy"
    assert "immutable execution plan" in selected["profile_reason"]
