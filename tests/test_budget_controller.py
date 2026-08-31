from __future__ import annotations

from ai_harness.economics import BudgetAction, BudgetController, BudgetUsage


LIMITS = {
    "max_model_calls": 8,
    "max_uncached_input_tokens": 80_000,
    "max_output_tokens": 25_000,
    "max_duration_seconds": 1_800,
    "max_repair_attempts": 3,
    "max_model_escalations": 2,
}


def test_budget_controller_progresses_from_normal_to_economy_to_optional_skip() -> None:
    controller = BudgetController(LIMITS)

    normal = controller.assess(BudgetUsage(model_calls=2), mandatory_role=False)
    economy = controller.assess(BudgetUsage(model_calls=6), mandatory_role=True)
    skip = controller.assess(BudgetUsage(model_calls=7, output_tokens=23_000), mandatory_role=False)

    assert normal.action == BudgetAction.CONTINUE
    assert economy.action == BudgetAction.ECONOMY
    assert skip.action == BudgetAction.SKIP_OPTIONAL


def test_soft_budget_exhaustion_keeps_mandatory_gate_running_in_economy() -> None:
    mandatory = BudgetController(LIMITS).assess(
        BudgetUsage(model_calls=8),
        mandatory_role=True,
    )
    optional = BudgetController(LIMITS).assess(
        BudgetUsage(model_calls=8),
        mandatory_role=False,
    )

    assert mandatory.action == BudgetAction.ECONOMY
    assert mandatory.exhausted_dimensions == ("model_calls",)
    assert optional.action == BudgetAction.SKIP_OPTIONAL


def test_hard_budget_exhaustion_still_requires_approval() -> None:
    decision = BudgetController(LIMITS).assess(
        BudgetUsage(elapsed_seconds=1_800),
        mandatory_role=True,
    )

    assert decision.action == BudgetAction.REQUIRE_APPROVAL
    assert decision.exhausted_dimensions == ("elapsed_seconds",)


def test_usage_counts_uncached_tokens_model_calls_repairs_and_escalations() -> None:
    state = {
        "elapsed_seconds": 42,
        "loops": {"quality_repair": {"iterations": 2}},
        "roles": [
            {
                "llm_invoked": True,
                "execution_profile": {"escalation_level": 1},
                "result": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 600,
                    "output_tokens": 200,
                },
            },
            {"llm_invoked": False, "result": {"tokens_used": 0}},
        ],
    }

    usage = BudgetUsage.from_state(state)

    assert usage.model_calls == 1
    assert usage.uncached_input_tokens == 400
    assert usage.output_tokens == 200
    assert usage.repair_attempts == 2
    assert usage.model_escalations == 1


def test_enforcement_usage_applies_only_approved_hard_budget_extensions() -> None:
    state = {
        "elapsed_seconds": 2_100,
        "loops": {"review_repair": {"iterations": 3}},
        "roles": [
            {
                "llm_invoked": True,
                "result": {"input_tokens": 1000, "cached_input_tokens": 250},
            }
        ],
        "adaptive_budget_extensions": [
            {
                "approval_id": "duration-extension",
                "dimensions": ["elapsed_seconds", "repair_attempts"],
                "baselines": {"elapsed_seconds": 1_800, "repair_attempts": 2},
            }
        ],
    }

    usage = BudgetUsage.for_enforcement(state)

    assert usage.elapsed_seconds == 300
    assert usage.repair_attempts == 1
    assert usage.model_calls == 1
    assert usage.uncached_input_tokens == 750


def test_enforcement_usage_does_not_extend_an_unapproved_dimension() -> None:
    state = {
        "elapsed_seconds": 2_100,
        "loops": {"review_repair": {"iterations": 3}},
        "adaptive_budget_extensions": [
            {
                "approval_id": "duration-only",
                "dimensions": ["elapsed_seconds"],
                "baselines": {"elapsed_seconds": 1_800, "repair_attempts": 2},
            }
        ],
    }

    usage = BudgetUsage.for_enforcement(state)

    assert usage.elapsed_seconds == 300
    assert usage.repair_attempts == 3
