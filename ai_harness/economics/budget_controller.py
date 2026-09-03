"""Bound task cost without weakening mandatory verification or security gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence

from ai_harness.execution_accounting import (
    accounted_checkpoints,
    role_entry_invoked_model,
    safe_int,
)


class BudgetAction(StrEnum):
    CONTINUE = "continue"
    ECONOMY = "economy"
    SKIP_OPTIONAL = "skip_optional"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class BudgetUsage:
    model_calls: int = 0
    uncached_input_tokens: int = 0
    output_tokens: int = 0
    elapsed_seconds: int = 0
    repair_attempts: int = 0
    model_escalations: int = 0
    cached_input_tokens: int = 0
    input_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    @property
    def cached_input_ratio(self) -> float:
        return self.cached_input_tokens / self.input_tokens if self.input_tokens else 0.0

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "BudgetUsage":
        roles = state.get("roles", [])
        checkpoints = accounted_checkpoints(roles)
        input_tokens = 0
        cached_tokens = 0
        output_tokens = 0
        model_calls = 0
        escalations = 0
        for checkpoint in checkpoints:
            result = checkpoint.get("result", {})
            if not isinstance(result, dict):
                continue
            input_tokens += safe_int(result.get("input_tokens", 0))
            cached_tokens += safe_int(result.get("cached_input_tokens", 0))
            output_tokens += safe_int(result.get("output_tokens", 0))
            if role_entry_invoked_model(checkpoint):
                model_calls += 1
            profile = checkpoint.get("execution_profile", {})
            if not isinstance(profile, dict):
                profile = {}
            escalation = result.get("escalation_level", profile.get("escalation_level", 0))
            if isinstance(escalation, (int, float)) and escalation > 0:
                escalations += 1
        loops = state.get("loops", {})
        repair_attempts = sum(
            int(value.get("iterations", 0) or 0)
            for value in loops.values()
            if isinstance(value, dict)
        ) if isinstance(loops, dict) else 0
        return cls(
            model_calls=model_calls,
            uncached_input_tokens=max(0, input_tokens - cached_tokens),
            output_tokens=output_tokens,
            elapsed_seconds=int(state.get("elapsed_seconds", 0) or 0),
            repair_attempts=repair_attempts,
            model_escalations=escalations,
            cached_input_tokens=cached_tokens,
            input_tokens=input_tokens,
        )


@dataclass(frozen=True)
class BudgetDecision:
    action: BudgetAction
    reason: str
    pressure: float
    exhausted_dimensions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "pressure": round(self.pressure, 6),
            "exhausted_dimensions": list(self.exhausted_dimensions),
        }


class BudgetController:
    """Apply ceiling budgets; a budget is never a target to fill."""

    FIELDS = {
        "model_calls": "max_model_calls",
        "uncached_input_tokens": "max_uncached_input_tokens",
        "output_tokens": "max_output_tokens",
        "elapsed_seconds": "max_duration_seconds",
        "repair_attempts": "max_repair_attempts",
        "model_escalations": "max_model_escalations",
    }
    SOFT_FIELDS = frozenset(
        {"model_calls", "uncached_input_tokens", "output_tokens"}
    )

    def __init__(self, limits: Mapping[str, int]) -> None:
        self.limits = {
            field: int(limits[limit])
            for field, limit in self.FIELDS.items()
            if isinstance(limits.get(limit), int) and int(limits[limit]) > 0
        }
        missing = sorted(set(self.FIELDS) - set(self.limits))
        if missing:
            raise ValueError("budget limits are missing: " + ", ".join(missing))

    @classmethod
    def from_plan(cls, plan: Mapping[str, Any]) -> "BudgetController":
        budgets = plan.get("budgets", {})
        if not isinstance(budgets, dict):
            raise ValueError("execution plan budgets must be an object")
        return cls(budgets)

    def assess(self, usage: BudgetUsage, *, mandatory_role: bool) -> BudgetDecision:
        ratios = {
            field: getattr(usage, field) / limit
            for field, limit in self.limits.items()
        }
        pressure = max(ratios.values(), default=0.0)
        exhausted = tuple(sorted(field for field, ratio in ratios.items() if ratio >= 1.0))
        hard_exhausted = tuple(
            field for field in exhausted if field not in self.SOFT_FIELDS
        )
        if hard_exhausted:
            return BudgetDecision(
                BudgetAction.REQUIRE_APPROVAL,
                "A hard execution bound is exhausted; mandatory gates remain pending and require explicit approval to continue.",
                pressure,
                hard_exhausted,
            )
        if exhausted and not mandatory_role:
            return BudgetDecision(
                BudgetAction.SKIP_OPTIONAL,
                "A soft task cost ceiling is exceeded; omit this optional role.",
                pressure,
                exhausted,
            )
        if exhausted:
            return BudgetDecision(
                BudgetAction.ECONOMY,
                "A soft task cost ceiling is exceeded; mandatory completion continues with the cheapest sufficient configured profile.",
                pressure,
                exhausted,
            )
        if pressure >= 0.9 and not mandatory_role:
            return BudgetDecision(
                BudgetAction.SKIP_OPTIONAL,
                "Task budget is near its ceiling; omit this optional role.",
                pressure,
            )
        if pressure >= 0.75:
            return BudgetDecision(
                BudgetAction.ECONOMY,
                "Task budget pressure requests the cheapest sufficient configured profile.",
                pressure,
            )
        return BudgetDecision(BudgetAction.CONTINUE, "Task budget has sufficient headroom.", pressure)


def aggregate_efficiency_metrics(states: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Aggregate the milestone KPIs over successful task states."""

    usages = [BudgetUsage.from_state(state) for state in states]
    successful = [
        usage
        for state, usage in zip(states, usages, strict=True)
        if state.get("execution_status") == "completed"
    ]
    total = max(1, len(usages))
    successful_count = max(1, len(successful))
    return {
        "model_calls_per_task": sum(item.model_calls for item in usages) / total,
        "model_calls_per_successful_task": sum(item.model_calls for item in successful) / successful_count,
        "input_tokens_per_task": sum(item.input_tokens for item in usages) / total,
        "uncached_input_tokens_per_task": sum(item.uncached_input_tokens for item in usages) / total,
        "cached_input_ratio": (
            sum(item.cached_input_tokens for item in usages)
            / max(1, sum(item.input_tokens for item in usages))
        ),
        "model_escalations_per_task": sum(item.model_escalations for item in usages) / total,
        "repair_attempts_per_task": sum(item.repair_attempts for item in usages) / total,
        "successful_task_token_cost": sum(
            item.uncached_input_tokens + item.output_tokens for item in successful
        ) / successful_count,
        "successful_task_latency": sum(item.elapsed_seconds for item in successful) / successful_count,
    }
