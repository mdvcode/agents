"""Deterministic model-profile selection inside one configured runtime provider."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .paths import harness_home

RUNTIME_CONFIG = harness_home() / ".agent-runtime.yaml"
PROFILE_NAMES = {"complex", "balanced", "economy"}
COMPLEX_AREAS = {
    "architecture",
    "auth",
    "billing",
    "database",
    "deployment",
    "migrations",
    "payments",
    "permissions",
    "production",
    "public_api",
    "security",
}
COMPLEX_HINTS = (
    "architecture",
    "authentication",
    "authorization",
    "billing",
    "database schema",
    "migration",
    "payment",
    "permission",
    "production",
    "public api",
    "refactor",
    "security",
    "архитектур",
    "авторизац",
    "аутентификац",
    "безопасност",
    "миграц",
    "оплат",
    "продакш",
    "рефактор",
)
MECHANICAL_HINTS = (
    "classify",
    "format",
    "formatting",
    "lint fix",
    "rename",
    "sort imports",
    "typo",
    "классифиц",
    "опечат",
    "переимен",
    "форматир",
)
ECONOMY_ROLES = {"risk-classifier", "report-agent"}
INHERENTLY_COMPLEX_ROLES = {
    "architecture-consistency-agent",
    "semantic-conflict-agent",
}
REASONING_FAILURES = {
    "invalid_output",
    "invalid_solution",
    "planner_uncertainty",
    "reasoning_failure",
    "repeated_invalid_solution",
}
DETERMINISTIC_FAILURES = {
    "ci_failure",
    "lint_failure",
    "test_failure",
    "type_failure",
}
COMPLEX_CAPABILITIES = {"architecture", "deep_review", "security_reasoning"}
REASONING_LADDERS = {
    "economy": ("low", "medium"),
    "balanced": ("medium", "high"),
    "complex": ("high", "xhigh"),
}


class ModelPolicyError(ValueError):
    """Raised when deterministic execution-profile configuration is invalid."""


def load_execution_profiles(path: Path = RUNTIME_CONFIG) -> dict[str, dict[str, str]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ModelPolicyError(f"cannot load runtime execution profiles: {exc}") from exc
    runtime = document.get("runtime", {}) if isinstance(document, dict) else {}
    profiles = runtime.get("execution_profiles", {}) if isinstance(runtime, dict) else {}
    if not isinstance(profiles, dict) or set(profiles) != PROFILE_NAMES:
        raise ModelPolicyError(
            "runtime execution_profiles must define complex, balanced, and economy"
        )
    normalized: dict[str, dict[str, str]] = {}
    for name in sorted(PROFILE_NAMES):
        value = profiles.get(name)
        if not isinstance(value, dict):
            raise ModelPolicyError(f"execution profile {name!r} must be an object")
        profile = {
            "model": str(value.get("model", "")),
            "reasoning_effort": str(value.get("reasoning_effort", "")),
            "service_tier": str(value.get("service_tier", "")),
        }
        if not all(profile.values()):
            raise ModelPolicyError(f"execution profile {name!r} is incomplete")
        normalized[name] = profile
    return normalized


def _normalized_areas(changed_areas: set[str] | list[str]) -> set[str]:
    return {
        re.sub(r"[^a-z0-9_]+", "_", str(value).casefold()).strip("_")
        for value in changed_areas
        if str(value).strip()
    }


def select_execution_profile(
    *,
    role: str,
    goal: str,
    risk_class: str,
    changed_files: list[str],
    changed_lines: int,
    changed_areas: set[str] | list[str],
    repair_iteration: int = 0,
    prior_failure: bool = False,
    previous_profile: str = "",
    previous_reasoning_effort: str = "",
    planned_profile: str = "",
    task_complexity: str = "",
    failure_type: str = "",
    context_size: int = 0,
    eval_success_rate: float | None = None,
    required_capability: str = "",
    budget_pressure: bool = False,
    repair_count: int | None = None,
    max_escalations: int = 2,
    human_escalation_approved: bool = False,
    bounded_escalation_exhausted: bool = False,
    profiles: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Select an auditable profile without changing provider or discovering models."""

    configured = profiles or load_execution_profiles()
    normalized_goal = goal.casefold()
    areas = _normalized_areas(changed_areas)
    large_change = len(changed_files) > 5 or changed_lines > 200
    complex_scope = (
        risk_class == "high"
        or large_change
        or bool(areas & COMPLEX_AREAS)
        or any(hint in normalized_goal for hint in COMPLEX_HINTS)
    )
    mechanical = any(hint in normalized_goal for hint in MECHANICAL_HINTS)

    profile_name = "balanced"
    reason = "ordinary local model-backed work uses the balanced profile"
    escalation_level = 0
    terminal_action = ""
    selected_effort = ""
    if planned_profile in PROFILE_NAMES:
        profile_name = planned_profile
        reason = f"the immutable execution plan selected {planned_profile} for {task_complexity or 'this task'} scope"
    if role in {"implementation-agent", "ci-repair-agent"} and repair_iteration == 1 and not complex_scope:
        profile_name = "economy"
        reason = "the first bounded repair is narrow and uses the economy profile"
    elif role in {"implementation-agent", "ci-repair-agent"} and complex_scope:
        profile_name = "complex"
        reason = "implementation scope is complex, large, sensitive, or high risk"
    elif role == "implementation-agent" and mechanical and len(changed_files) <= 2:
        profile_name = "economy"
        reason = "mechanical local implementation uses the economy profile"
    elif role == "reviewer" and (risk_class in {"medium", "high"} or large_change):
        profile_name = "complex"
        reason = "review is risk-bearing or large and requires the complex profile"
    elif role in INHERENTLY_COMPLEX_ROLES:
        profile_name = "complex"
        reason = "this optional verifier is activated only for complex impact"
    elif role in ECONOMY_ROLES:
        profile_name = "economy"
        reason = "mechanical classification/reporting uses the economy profile"

    normalized_failure = failure_type.casefold().strip()
    effective_repairs = repair_iteration if repair_count is None else repair_count
    capability_requires_complex = required_capability in COMPLEX_CAPABILITIES
    eval_requires_upgrade = (
        isinstance(eval_success_rate, (int, float)) and float(eval_success_rate) < 0.7
    )
    context_requires_complex = context_size > 16_000
    reasoning_failure = normalized_failure in REASONING_FAILURES
    deterministic_failure = normalized_failure in DETERMINISTIC_FAILURES
    legacy_failure = prior_failure and not normalized_failure
    repeated_failure = effective_repairs >= 2
    if (
        bounded_escalation_exhausted
        and role in {"implementation-agent", "ci-repair-agent"}
    ):
        profile_name = "complex"
        escalation_level = min(max_escalations, 2)
        if human_escalation_approved:
            selected_effort = REASONING_LADDERS[profile_name][-1]
            reason = "an explicit one-role approval authorizes the final bounded model attempt"
        else:
            selected_effort = configured[profile_name]["reasoning_effort"]
            terminal_action = "human_or_dead_letter"
            reason = "the bounded model ladder is exhausted and requires human review or dead-letter"
    elif capability_requires_complex or context_requires_complex or eval_requires_upgrade:
        profile_name = "complex"
        reason = "required capability, context size, or eval history requires the complex profile"
    elif deterministic_failure:
        if previous_profile in PROFILE_NAMES:
            profile_name = previous_profile
        reason = "deterministic check failure stays in the repair loop without model escalation"
    elif reasoning_failure or legacy_failure or repeated_failure:
        ladder_start = previous_profile if previous_profile in PROFILE_NAMES else profile_name
        ladder = REASONING_LADDERS[ladder_start]
        current_effort = previous_reasoning_effort or configured[ladder_start]["reasoning_effort"]
        if current_effort in ladder and ladder.index(current_effort) + 1 < len(ladder):
            profile_name = ladder_start
            selected_effort = ladder[ladder.index(current_effort) + 1]
            escalation_level = 1
            reason = f"{normalized_failure or 'prior model failure'} increased reasoning effort before changing model"
        elif ladder_start == "economy":
            profile_name = "balanced"
            selected_effort = configured[profile_name]["reasoning_effort"]
            escalation_level = 1
            reason = f"{normalized_failure or 'prior model failure'} upgraded the bounded model ladder"
        elif ladder_start == "balanced":
            profile_name = "complex"
            selected_effort = configured[profile_name]["reasoning_effort"]
            escalation_level = 1
            reason = f"{normalized_failure or 'prior model failure'} upgraded the bounded model ladder"
        else:
            profile_name = "complex"
            escalation_level = min(max_escalations, 2)
            if human_escalation_approved:
                selected_effort = REASONING_LADDERS[profile_name][-1]
                reason = "an explicit one-role approval authorizes the final bounded model attempt"
            else:
                selected_effort = configured[profile_name]["reasoning_effort"]
                terminal_action = "human_or_dead_letter"
                reason = "the bounded model ladder is exhausted and requires human review or dead-letter"
    elif budget_pressure and profile_name == "balanced" and not complex_scope:
        profile_name = "economy"
        reason = "task budget pressure selected the cheapest sufficient profile"

    escalation_level = min(escalation_level, max(0, max_escalations))

    selected = configured[profile_name]
    reasoning_effort = selected_effort or selected["reasoning_effort"]
    return {
        "execution_profile": profile_name,
        "model": selected["model"],
        "reasoning_effort": reasoning_effort,
        "service_tier": selected["service_tier"],
        "profile_reason": reason,
        "escalation_level": escalation_level,
        "terminal_action": terminal_action,
    }


def validate_request_profile(
    request: dict[str, Any],
    *,
    profiles: dict[str, dict[str, str]] | None = None,
) -> dict[str, str]:
    """Reject request-side model overrides that are not an exact configured profile."""

    configured = profiles or load_execution_profiles()
    name = str(request.get("execution_profile", "balanced") or "balanced")
    if name not in configured:
        raise ModelPolicyError(f"unknown execution profile: {name!r}")
    expected = configured[name]
    for field in ("model", "service_tier"):
        requested = request.get(field)
        if requested is not None and str(requested) != expected[field]:
            raise ModelPolicyError(
                f"execution profile {name!r} requires {field}={expected[field]!r}"
            )
    effort = str(request.get("reasoning_effort", expected["reasoning_effort"]))
    if effort not in REASONING_LADDERS[name]:
        raise ModelPolicyError(
            f"execution profile {name!r} does not allow reasoning_effort={effort!r}"
        )
    return {"execution_profile": name, **expected, "reasoning_effort": effort}
