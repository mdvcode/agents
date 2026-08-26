"""Immutable contracts for compiled adaptive execution DAGs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from collections.abc import Mapping as MappingABC
from typing import Any, Mapping


def _freeze(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return sorted((_thaw(item) for item in value), key=str)
    return value


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _freeze(value)


@dataclass(frozen=True)
class PlanBudget:
    max_model_calls: int
    max_uncached_input_tokens: int
    max_output_tokens: int
    max_duration_seconds: int
    max_repair_attempts: int = 3
    max_model_escalations: int = 2

    def __post_init__(self) -> None:
        for field, value in self.as_dict().items():
            if value < 1:
                raise ValueError(f"budget {field} must be positive")

    def as_dict(self) -> dict[str, int]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_uncached_input_tokens": self.max_uncached_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_duration_seconds": self.max_duration_seconds,
            "max_repair_attempts": self.max_repair_attempts,
            "max_model_escalations": self.max_model_escalations,
        }


@dataclass(frozen=True)
class ExecutionNode:
    id: str
    role: str
    execution_kind: str
    mandatory: bool
    read_only: bool
    dependencies: tuple[str, ...] = ()
    deterministic_checks: tuple[str, ...] = ()
    model_profile: str = ""
    context_budget: int = 0
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "role": self.role,
            "execution_kind": self.execution_kind,
            "mandatory": self.mandatory,
            "read_only": self.read_only,
            "dependencies": list(self.dependencies),
            "deterministic_checks": list(self.deterministic_checks),
            "model_profile": self.model_profile,
            "context_budget": self.context_budget,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExecutionPlan:
    workflow_version: int
    task_id: str
    mode: str
    analysis: Mapping[str, Any]
    nodes: tuple[ExecutionNode, ...]
    edges: tuple[tuple[str, str], ...]
    parallel_groups: tuple[tuple[str, ...], ...]
    required_roles: tuple[str, ...]
    skipped_roles: tuple[str, ...]
    deterministic_checks: tuple[str, ...]
    model_profiles: Mapping[str, str]
    context_budgets: Mapping[str, int]
    budgets: PlanBudget
    estimated_max_model_calls: int
    reasoning: Mapping[str, str]
    policy_version: str
    compiler_version: str
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "analysis", _frozen_mapping(self.analysis))
        object.__setattr__(self, "model_profiles", _frozen_mapping(self.model_profiles))
        object.__setattr__(self, "context_budgets", _frozen_mapping(self.context_budgets))
        object.__setattr__(self, "reasoning", _frozen_mapping(self.reasoning))
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("execution plan node ids must be unique")
        known = set(node_ids)
        if any(source not in known or target not in known for source, target in self.edges):
            raise ValueError("execution plan edges must reference known nodes")
        if any(role in self.skipped_roles for role in self.required_roles):
            raise ValueError("required and skipped roles must be disjoint")

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        mode: str,
        analysis: Mapping[str, Any],
        nodes: tuple[ExecutionNode, ...],
        parallel_groups: tuple[tuple[str, ...], ...],
        skipped_roles: tuple[str, ...],
        deterministic_checks: tuple[str, ...],
        model_profiles: Mapping[str, str],
        context_budgets: Mapping[str, int],
        budgets: PlanBudget,
        reasoning: Mapping[str, str],
        policy_version: str,
        compiler_version: str = "1",
    ) -> "ExecutionPlan":
        edges = tuple(
            (dependency, node.id)
            for node in nodes
            for dependency in node.dependencies
        )
        required_roles = tuple(dict.fromkeys(node.role for node in nodes if node.role))
        model_calls = sum(node.execution_kind == "llm_role" for node in nodes)
        return cls(
            workflow_version=1,
            task_id=task_id,
            mode=mode,
            analysis=dict(analysis),
            nodes=nodes,
            edges=edges,
            parallel_groups=parallel_groups,
            required_roles=required_roles,
            skipped_roles=skipped_roles,
            deterministic_checks=deterministic_checks,
            model_profiles=dict(model_profiles),
            context_budgets=dict(context_budgets),
            budgets=budgets,
            estimated_max_model_calls=model_calls,
            reasoning=dict(reasoning),
            policy_version=policy_version,
            compiler_version=compiler_version,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def node(self, node_id: str) -> ExecutionNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def as_dict(self) -> dict[str, object]:
        return {
            "workflow_version": self.workflow_version,
            "task_id": self.task_id,
            "mode": self.mode,
            "analysis": _thaw(self.analysis),
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [{"from": source, "to": target} for source, target in self.edges],
            "parallel_groups": [list(group) for group in self.parallel_groups],
            "required_roles": list(self.required_roles),
            "skipped_roles": list(self.skipped_roles),
            "deterministic_checks": list(self.deterministic_checks),
            "model_profiles": _thaw(self.model_profiles),
            "context_budgets": _thaw(self.context_budgets),
            "budgets": self.budgets.as_dict(),
            "estimated_max_model_calls": self.estimated_max_model_calls,
            "reasoning": _thaw(self.reasoning),
            "policy_version": self.policy_version,
            "compiler_version": self.compiler_version,
            "created_at": self.created_at,
        }
