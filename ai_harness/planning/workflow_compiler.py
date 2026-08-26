"""Compile deterministic task facts into a minimum-safe execution DAG."""

from __future__ import annotations

from dataclasses import dataclass

from .execution_plan import ExecutionNode, ExecutionPlan, PlanBudget
from .parallelism import validate_parallel_groups
from .role_policy import RolePolicy
from .task_analyzer import TaskAnalysis


COMPILER_VERSION = "1"
MODEL_ROLES = {
    "planner",
    "implementation-agent",
    "security-agent",
    "test-generator",
    "frontend-qa-agent",
    "architecture-consistency-agent",
    "semantic-conflict-agent",
    "reviewer",
    "report-agent",
}


@dataclass(frozen=True)
class _RoleDecision:
    required: bool
    reason: str


class WorkflowCompiler:
    """Apply role policy without weakening mandatory Harness gates."""

    def __init__(self, policy: RolePolicy) -> None:
        self.policy = policy

    def compile(
        self,
        analysis: TaskAnalysis,
        *,
        task_id: str,
        mode: str = "adaptive",
        project_profile: str = "agent_workspace",
    ) -> ExecutionPlan:
        if mode not in {"adaptive", "full", "fast"}:
            raise ValueError(f"unsupported execution-plan mode: {mode}")
        if mode == "full" or analysis.confidence < self.policy.confidence_threshold:
            selected = list(self.policy.full_chain)
            effective_mode = "full" if mode == "full" else "adaptive_safe_fallback"
            fallback_reason = (
                "explicit full workflow"
                if mode == "full"
                else f"analysis confidence {analysis.confidence:.2f} is below {self.policy.confidence_threshold:.2f}"
            )
            decisions = {role: _RoleDecision(True, fallback_reason) for role in selected}
        else:
            effective_mode = mode
            decisions = self._adaptive_decisions(analysis, mode)
            selected = [
                role
                for role in self.policy.adaptive_candidates
                if decisions.get(role, _RoleDecision(False, "not selected")).required
            ]
        for hard_gate in self.policy.hard_gate_roles:
            if hard_gate not in selected:
                selected.append(hard_gate)
                decisions[hard_gate] = _RoleDecision(True, "hard gate cannot be skipped")
        selected = self._ordered(selected)
        nodes = self._nodes(
            selected,
            decisions,
            analysis,
            project_profile=project_profile,
        )
        groups = self._parallel_groups(nodes)
        all_roles = tuple(dict.fromkeys(self.policy.full_chain + self.policy.adaptive_candidates))
        skipped = tuple(role for role in all_roles if role not in selected)
        available_checks = set(analysis.deterministic_tools)
        configured_checks = tuple(self.policy.deterministic_checks.get(project_profile, ()))
        checks = tuple(check for check in configured_checks if check in available_checks)
        profiles = {
            node.role: node.model_profile
            for node in nodes
            if node.model_profile
        }
        context_budgets = {
            node.role: node.context_budget
            for node in nodes
            if node.context_budget
        }
        budget_values = self.policy.task_budgets.get(
            analysis.scope,
            self.policy.task_budgets.get("medium", {}),
        )
        budgets = PlanBudget(
            max_model_calls=int(budget_values.get("max_model_calls", 8)),
            max_uncached_input_tokens=int(budget_values.get("max_uncached_input_tokens", 80_000)),
            max_output_tokens=int(budget_values.get("max_output_tokens", 25_000)),
            max_duration_seconds=int(budget_values.get("max_duration_seconds", 1_800)),
            max_repair_attempts=int(budget_values.get("max_repair_attempts", 3)),
            max_model_escalations=int(budget_values.get("max_model_escalations", 2)),
        )
        reasoning = {
            role: decision.reason
            for role, decision in decisions.items()
        }
        reasoning.update(
            {
                role: self._skip_reason(role, analysis)
                for role in skipped
                if role not in reasoning
            }
        )
        plan = ExecutionPlan.create(
            task_id=task_id,
            mode=effective_mode,
            analysis=analysis.as_dict(),
            nodes=nodes,
            parallel_groups=groups,
            skipped_roles=skipped,
            deterministic_checks=checks,
            model_profiles=profiles,
            context_budgets=context_budgets,
            budgets=budgets,
            reasoning=reasoning,
            policy_version=self.policy.version,
            compiler_version=COMPILER_VERSION,
        )
        validate_parallel_groups(plan)
        if not set(self.policy.hard_gate_roles) <= set(plan.required_roles):
            raise ValueError("compiled plan omitted a hard gate")
        return plan

    def _adaptive_decisions(self, analysis: TaskAnalysis, mode: str) -> dict[str, _RoleDecision]:
        indicators = set(analysis.indicators)
        indicators.update(
            {
                f"task_class:{analysis.task_class}",
                f"scope:{analysis.scope}",
                f"risk:{analysis.risk}",
            }
        )
        if analysis.requires_code_change:
            indicators.add("code_change")
        if analysis.requires_tests:
            indicators.add("tests_required")
        if analysis.requires_security_review:
            indicators.add("security_review_required")
        if analysis.requires_architecture_review:
            indicators.add("architecture_review_required")
        if analysis.requires_frontend_verification:
            indicators.add("frontend_verification_required")
        if analysis.requires_semantic_review:
            indicators.add("semantic_review_required")
        decisions: dict[str, _RoleDecision] = {}
        for role in self.policy.adaptive_candidates:
            rule = self.policy.rule(role)
            required = self.policy.role_required(role, indicators)
            matches = sorted(set(rule.required_when) & indicators)
            skips = sorted(set(rule.skip_when) & indicators)
            if role in self.policy.hard_gate_roles or rule.mandatory:
                reason = "mandatory Harness or publication gate"
            elif required:
                reason = f"matched: {', '.join(matches)}"
            elif skips:
                reason = f"skipped by: {', '.join(skips)}"
            else:
                reason = "no role-policy requirement matched"
            decisions[role] = _RoleDecision(required, reason)
        if mode == "fast":
            for role in ("planner", "test-generator", "frontend-qa-agent", "architecture-consistency-agent", "semantic-conflict-agent"):
                if role in decisions and role not in self.policy.hard_gate_roles:
                    decisions[role] = _RoleDecision(False, "explicit fast policy")
        return decisions

    def _ordered(self, selected: list[str]) -> list[str]:
        order = {role: index for index, role in enumerate(self.policy.full_chain)}
        return sorted(dict.fromkeys(selected), key=lambda role: order.get(role, len(order)))

    def _nodes(
        self,
        selected: list[str],
        decisions: dict[str, _RoleDecision],
        analysis: TaskAnalysis,
        *,
        project_profile: str,
    ) -> tuple[ExecutionNode, ...]:
        nodes: list[ExecutionNode] = []
        prior_write_or_setup = ""
        verification_roles = {
            "quality-runner",
            "security-agent",
            "frontend-qa-agent",
            "architecture-consistency-agent",
            "semantic-conflict-agent",
        }
        first_verifier = min(
            (selected.index(role) for role in verification_roles if role in selected),
            default=len(selected),
        )
        verification_frontier = next(
            (
                role
                for role in reversed(selected[:first_verifier])
                if not self.policy.rule(role).read_only
            ),
            "context-compiler",
        )
        for role in selected:
            rule = self.policy.rule(role)
            kind = rule.execution_kind
            analysis_indicators = set(analysis.indicators)
            deterministic_only = bool(
                set(rule.deterministic_only_when) & analysis_indicators
            )
            if deterministic_only:
                kind = "harness_stage"
            elif role in MODEL_ROLES and kind != "harness_stage":
                kind = "llm_role"
            dependencies: tuple[str, ...]
            if role in verification_roles:
                dependencies = (verification_frontier,) if verification_frontier in selected else ()
            elif role == "reviewer":
                dependencies = tuple(item for item in selected if item in verification_roles)
            elif role == "orchestrator":
                dependencies = ("reviewer",) if "reviewer" in selected else tuple(
                    item for item in selected if item in verification_roles
                )
            elif role == "publication-prepare":
                dependencies = ("orchestrator",) if "orchestrator" in selected else ()
            else:
                dependencies = (prior_write_or_setup,) if prior_write_or_setup else ()
            profile = self._model_profile(role, analysis) if kind == "llm_role" else ""
            available_checks = set(analysis.deterministic_tools)
            checks = (
                tuple(
                    check
                    for check in self.policy.deterministic_checks.get(project_profile, ())
                    if check in available_checks
                )
                if role == "quality-runner"
                else tuple(
                    check
                    for check in ("secret_scan", "dependency_audit")
                    if check in available_checks
                    and (check != "dependency_audit" or "dependency_change" in analysis.indicators)
                )
                if role == "security-agent"
                else ()
            )
            nodes.append(
                ExecutionNode(
                    id=role,
                    role=role,
                    execution_kind=kind,
                    mandatory=role in self.policy.hard_gate_roles or rule.mandatory,
                    read_only=rule.read_only,
                    dependencies=dependencies,
                    deterministic_checks=checks,
                    model_profile=profile,
                    context_budget=int(self.policy.context_budgets.get(role, 0)),
                    reason=(
                        decisions.get(role, _RoleDecision(True, "selected")).reason
                        + (
                            "; deterministic evidence is sufficient under role policy"
                            if deterministic_only
                            else "; model reasoning is required after deterministic preflight"
                            if role in MODEL_ROLES
                            else ""
                        )
                    ),
                )
            )
            if role not in verification_roles and not rule.read_only:
                prior_write_or_setup = role
            elif role in {"issue-intake", "context-compiler", "planner"}:
                prior_write_or_setup = role
        return tuple(nodes)

    @staticmethod
    def _parallel_groups(nodes: tuple[ExecutionNode, ...]) -> tuple[tuple[str, ...], ...]:
        grouped: dict[tuple[str, ...], list[str]] = {}
        for node in nodes:
            if node.read_only and node.role in {
                "quality-runner",
                "security-agent",
                "frontend-qa-agent",
                "architecture-consistency-agent",
                "semantic-conflict-agent",
            }:
                grouped.setdefault(node.dependencies, []).append(node.id)
        return tuple(tuple(group) for group in grouped.values() if len(group) > 1)

    @staticmethod
    def _model_profile(role: str, analysis: TaskAnalysis) -> str:
        if analysis.risk == "high" or analysis.scope == "large" or role in {
            "architecture-consistency-agent",
            "semantic-conflict-agent",
        }:
            return "complex"
        if analysis.scope == "trivial" or role in {"report-agent"}:
            return "economy"
        return "balanced"

    @staticmethod
    def _skip_reason(role: str, analysis: TaskAnalysis) -> str:
        labels = {
            "frontend-qa-agent": "no frontend or browser-visible behavior detected",
            "architecture-consistency-agent": "no architecture boundary or cross-module change detected",
            "semantic-conflict-agent": "no public behavior, contract, or complex refactor detected",
            "test-generator": "tests are not required or implementation can provide a focused test directly",
            "planner": "task is sufficiently narrow and confidently classified",
        }
        return labels.get(role, f"not required for {analysis.task_class}/{analysis.scope}")
