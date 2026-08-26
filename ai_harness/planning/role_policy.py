"""Declarative role-selection policy for adaptive plans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from ai_harness.paths import harness_home


DEFAULT_POLICY_PATH = harness_home() / ".agent-role-policy.yaml"


class RolePolicyError(ValueError):
    """Raised when adaptive role policy is missing or unsafe."""


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RolePolicyError(f"{label} must be a list of strings")
    return tuple(value)


@dataclass(frozen=True)
class RoleRule:
    required_when: tuple[str, ...] = ()
    skip_when: tuple[str, ...] = ()
    deterministic_only_when: tuple[str, ...] = ()
    mandatory: bool = False
    read_only: bool = True
    execution_kind: str = "llm_role"


@dataclass(frozen=True)
class RolePolicy:
    version: str
    confidence_threshold: float
    full_chain: tuple[str, ...]
    adaptive_candidates: tuple[str, ...]
    hard_gate_roles: tuple[str, ...]
    rules: Mapping[str, RoleRule]
    context_budgets: Mapping[str, int]
    task_budgets: Mapping[str, Mapping[str, int]]
    deterministic_checks: Mapping[str, tuple[str, ...]]

    @classmethod
    def load(cls, path: Path = DEFAULT_POLICY_PATH) -> "RolePolicy":
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RolePolicyError(f"cannot load adaptive role policy: {exc}") from exc
        if not isinstance(document, dict):
            raise RolePolicyError("adaptive role policy must be an object")
        roles = document.get("roles")
        if not isinstance(roles, dict) or not roles:
            raise RolePolicyError("adaptive role policy must define roles")
        rules: dict[str, RoleRule] = {}
        for role, raw in roles.items():
            if not isinstance(role, str) or not isinstance(raw, dict):
                raise RolePolicyError("each adaptive role policy must be an object")
            kind = str(raw.get("execution_kind", "llm_role"))
            if kind not in {"llm_role", "harness_stage"}:
                raise RolePolicyError(f"role {role!r} has invalid execution_kind")
            rules[role] = RoleRule(
                required_when=_string_tuple(raw.get("required_when"), f"roles.{role}.required_when"),
                skip_when=_string_tuple(raw.get("skip_when"), f"roles.{role}.skip_when"),
                deterministic_only_when=_string_tuple(
                    raw.get("deterministic_only_when"),
                    f"roles.{role}.deterministic_only_when",
                ),
                mandatory=bool(raw.get("mandatory", False)),
                read_only=bool(raw.get("read_only", True)),
                execution_kind=kind,
            )
        hard_gates = _string_tuple(document.get("hard_gate_roles"), "hard_gate_roles")
        if any(role not in rules for role in hard_gates):
            raise RolePolicyError("hard_gate_roles must reference configured roles")
        budgets = document.get("context_budgets", {})
        if not isinstance(budgets, dict) or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 256
            for value in budgets.values()
        ):
            raise RolePolicyError("context_budgets must map roles to token ceilings >= 256")
        raw_task_budgets = document.get("task_budgets", {})
        if not isinstance(raw_task_budgets, dict):
            raise RolePolicyError("task_budgets must be an object")
        task_budgets: dict[str, Mapping[str, int]] = {}
        for scope, value in raw_task_budgets.items():
            if not isinstance(scope, str) or not isinstance(value, dict) or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 1
                for item in value.values()
            ):
                raise RolePolicyError(f"task_budgets.{scope} must contain positive integers")
            task_budgets[scope] = dict(value)
        raw_checks = document.get("deterministic_checks", {})
        if not isinstance(raw_checks, dict):
            raise RolePolicyError("deterministic_checks must be an object")
        checks = {
            str(profile): _string_tuple(value, f"deterministic_checks.{profile}")
            for profile, value in raw_checks.items()
        }
        threshold = document.get("confidence_threshold", 0.75)
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 <= float(threshold) <= 1:
            raise RolePolicyError("confidence_threshold must be between zero and one")
        return cls(
            version=str(document.get("version", "1")),
            confidence_threshold=float(threshold),
            full_chain=_string_tuple(document.get("full_chain"), "full_chain"),
            adaptive_candidates=_string_tuple(document.get("adaptive_candidates"), "adaptive_candidates"),
            hard_gate_roles=hard_gates,
            rules=rules,
            context_budgets={str(role): int(value) for role, value in budgets.items()},
            task_budgets=task_budgets,
            deterministic_checks=checks,
        )

    def rule(self, role: str) -> RoleRule:
        try:
            return self.rules[role]
        except KeyError as exc:
            raise RolePolicyError(f"role {role!r} is not configured") from exc

    def role_required(self, role: str, indicators: set[str]) -> bool:
        rule = self.rule(role)
        if role in self.hard_gate_roles or rule.mandatory:
            return True
        if set(rule.skip_when) & indicators:
            return False
        return bool(set(rule.required_when) & indicators)
