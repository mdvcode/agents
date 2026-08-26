from __future__ import annotations

from pathlib import Path

import pytest

from ai_harness.planning import (
    ParallelismError,
    RolePolicy,
    TaskAnalyzer,
    WorkflowCompiler,
    ready_nodes,
    validate_parallel_groups,
)
from ai_harness.planning.execution_plan import ExecutionNode, ExecutionPlan, PlanBudget


ROOT = Path(__file__).resolve().parents[1]


def compiler() -> WorkflowCompiler:
    return WorkflowCompiler(RolePolicy.load(ROOT / ".agent-role-policy.yaml"))


def test_small_bugfix_compiles_minimum_safe_dag() -> None:
    analysis = TaskAnalyzer().analyze(
        "Fix the local status formatter regression and add a focused test",
        repository_profile="agent_workspace",
        requested_paths=["ai_harness/cli.py", "tests/test_agent_cli.py"],
        deterministic_tools=["pytest", "ruff", "detect-secrets"],
    )

    plan = compiler().compile(
        analysis,
        task_id="fix-status",
        mode="adaptive",
        project_profile="agent_workspace",
    )

    assert analysis.task_class == "bugfix"
    assert analysis.scope == "small"
    assert analysis.risk == "low"
    assert plan.mode == "adaptive"
    assert "planner" not in plan.required_roles
    assert "risk-classifier" not in plan.required_roles
    assert set(plan.required_roles) >= {
        "implementation-agent",
        "quality-runner",
        "security-agent",
        "reviewer",
        "orchestrator",
        "publication-prepare",
    }
    assert set(plan.skipped_roles) >= {
        "frontend-qa-agent",
        "architecture-consistency-agent",
    }
    assert plan.model_profiles["implementation-agent"] == "balanced"
    assert plan.estimated_max_model_calls < 5


def test_low_confidence_uses_safe_full_fallback() -> None:
    analysis = TaskAnalyzer().analyze(
        "Do the thing",
        repository_profile="agent_workspace",
    )

    plan = compiler().compile(analysis, task_id="unknown", mode="adaptive")

    assert analysis.task_class == "unknown"
    assert plan.mode == "adaptive_safe_fallback"
    assert plan.required_roles == RolePolicy.load(ROOT / ".agent-role-policy.yaml").full_chain
    assert not plan.skipped_roles


def test_security_sensitive_task_cannot_skip_security_or_approval_path() -> None:
    analysis = TaskAnalyzer().analyze(
        "Change authentication permissions for the public API",
        repository_profile="django",
        requested_paths=["accounts/auth/service.py", "accounts/permissions/policy.py"],
    )

    plan = compiler().compile(analysis, task_id="auth-change", mode="adaptive")

    assert analysis.risk == "high"
    assert analysis.requires_security_review is True
    assert "security-agent" in plan.required_roles
    assert plan.node("security-agent").mandatory is True  # type: ignore[union-attr]
    assert "risk-classifier" not in plan.required_roles
    assert plan.analysis["risk"] == "high"
    assert plan.model_profiles["implementation-agent"] == "complex"


def test_independent_read_only_verifiers_share_parallel_frontier() -> None:
    analysis = TaskAnalyzer().analyze(
        "Refactor the public frontend contract across modules",
        repository_profile="nextjs_web",
        requested_paths=["app/page.tsx", "components/menu.tsx", "lib/contract.ts"],
        metadata={"scope": "medium"},
    )

    plan = compiler().compile(analysis, task_id="frontend-refactor", mode="adaptive", project_profile="nextjs_web")

    validate_parallel_groups(plan)
    parallel = [set(group) for group in plan.parallel_groups]
    assert any({"quality-runner", "security-agent", "frontend-qa-agent"} <= group for group in parallel)
    assert set(ready_nodes(plan, {"issue-intake", "context-compiler", "planner", "implementation-agent", "test-generator"})) >= {
        "quality-runner",
        "security-agent",
        "frontend-qa-agent",
    }


def test_parallelism_rejects_write_agents_in_same_worktree() -> None:
    plan = ExecutionPlan.create(
        task_id="unsafe",
        mode="adaptive",
        analysis={},
        nodes=(
            ExecutionNode("write-a", "implementation-agent", "llm_role", True, False),
            ExecutionNode("write-b", "test-generator", "llm_role", True, False),
        ),
        parallel_groups=(("write-a", "write-b"),),
        skipped_roles=(),
        deterministic_checks=(),
        model_profiles={},
        context_budgets={},
        budgets=PlanBudget(2, 1000, 1000, 100),
        reasoning={},
        policy_version="test",
    )

    with pytest.raises(ParallelismError, match="write nodes"):
        validate_parallel_groups(plan)


def test_execution_plan_is_deeply_read_only_for_mappings() -> None:
    analysis = TaskAnalyzer().analyze(
        "Fix typo",
        repository_profile="agent_workspace",
        requested_paths=["README.md"],
    )
    plan = compiler().compile(analysis, task_id="docs", mode="adaptive")

    with pytest.raises(TypeError):
        plan.reasoning["planner"] = "mutated"  # type: ignore[index]
    with pytest.raises(AttributeError):
        plan.analysis["domains"].append("mutated")  # type: ignore[union-attr]


def test_protected_or_production_change_is_high_risk() -> None:
    analysis = TaskAnalyzer().analyze(
        "Update the production billing handler",
        repository_profile="agent_workspace",
        requested_paths=["billing/handler.py"],
    )

    assert analysis.risk == "high"
    assert {"protected_path_change", "high_risk_operation"} <= set(analysis.indicators)


def test_verification_waits_for_optional_write_roles() -> None:
    analysis = TaskAnalyzer().analyze(
        "Add a medium feature with regression tests across several modules",
        repository_profile="agent_workspace",
        project_type="agent_workspace",
        requested_paths=["ai_harness/planning/workflow_compiler.py"],
        metadata={"scope": "medium"},
    )
    plan = compiler().compile(
        analysis,
        task_id="medium-feature",
        mode="adaptive",
        project_profile="agent_workspace",
    )

    assert "test-generator" in plan.required_roles
    for role in ("quality-runner", "security-agent"):
        node = plan.node(role)
        assert node is not None
        assert node.dependencies == ("test-generator",)


def test_security_policy_uses_scanners_for_low_risk_and_llm_for_auth_changes() -> None:
    analyzer = TaskAnalyzer()
    workflow_compiler = compiler()
    low = analyzer.analyze(
        "Fix a local formatter bug",
        repository_profile="agent_workspace",
        requested_paths=["ai_harness/formatting.py"],
        deterministic_tools=["pytest", "detect-secrets", "pip-audit"],
    )
    sensitive = analyzer.analyze(
        "Change authentication permissions",
        repository_profile="django",
        requested_paths=["accounts/auth/service.py"],
        deterministic_tools=["pytest", "detect-secrets", "pip-audit"],
    )

    low_security = workflow_compiler.compile(low, task_id="low").node("security-agent")
    sensitive_security = workflow_compiler.compile(
        sensitive,
        task_id="sensitive",
        project_profile="django",
    ).node("security-agent")

    assert low_security is not None and low_security.execution_kind == "harness_stage"
    assert set(low_security.deterministic_checks) == {"secret_scan"}
    assert sensitive_security is not None and sensitive_security.execution_kind == "llm_role"
    assert sensitive_security.model_profile == "complex"


def test_missing_test_runner_is_an_auditable_conservative_indicator() -> None:
    analysis = TaskAnalyzer().analyze(
        "Fix a local backend bug",
        repository_profile="agent_workspace",
        requested_paths=["ai_harness/worker.py"],
        deterministic_tools=["git"],
    )

    assert "deterministic_tool_gap" in analysis.indicators
    assert analysis.confidence < 0.9
