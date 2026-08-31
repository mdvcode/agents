from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_router import decide_next_role  # noqa: E402


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def plan(
    path: Path,
    *,
    max_model_calls: int = 5,
    max_duration_seconds: int = 1_800,
) -> None:
    write_json(
        path,
        {
            "required_roles": [
                "issue-intake",
                "context-compiler",
                "implementation-agent",
                "quality-runner",
                "security-agent",
                "reviewer",
                "orchestrator",
                "publication-prepare",
            ],
            "nodes": [
                {"id": "issue-intake", "role": "issue-intake", "dependencies": [], "mandatory": True},
                {"id": "context-compiler", "role": "context-compiler", "dependencies": ["issue-intake"], "mandatory": True},
                {"id": "implementation-agent", "role": "implementation-agent", "dependencies": ["context-compiler"], "mandatory": True},
                {"id": "quality-runner", "role": "quality-runner", "dependencies": ["implementation-agent"], "mandatory": True},
                {"id": "security-agent", "role": "security-agent", "dependencies": ["implementation-agent"], "mandatory": True},
                {"id": "reviewer", "role": "reviewer", "dependencies": ["quality-runner", "security-agent"], "mandatory": True},
                {"id": "orchestrator", "role": "orchestrator", "dependencies": ["reviewer"], "mandatory": True},
                {"id": "publication-prepare", "role": "publication-prepare", "dependencies": ["orchestrator"], "mandatory": True}
            ],
            "budgets": {
                "max_model_calls": max_model_calls,
                "max_uncached_input_tokens": 80000,
                "max_output_tokens": 25000,
                "max_duration_seconds": max_duration_seconds,
                "max_repair_attempts": 3,
                "max_model_escalations": 2
            }
        },
    )


def state(plan_path: Path, roles: list[str]) -> dict[str, object]:
    return {
        "effective_mode": "adaptive",
        "execution_plan_path": str(plan_path),
        "roles": [
            {
                "role": role,
                "llm_invoked": role == "implementation-agent",
                "result": {"status": "completed", "tokens_used": 0},
            }
            for role in roles
        ],
        "loops": {
            "security_repair": {"iterations": 0},
            "quality_repair": {"iterations": 0},
            "review_repair": {"iterations": 0},
            "ci_repair": {"iterations": 0},
            "frontend_verification_repair": {"iterations": 0},
        },
        "budgets": {
            "max_roles": 20,
            "max_repair_iterations": 6,
            "max_duration_seconds": 1800,
            "max_tokens": 600000,
        },
        "elapsed_seconds": 1,
        "role_count": len(roles),
        "tokens_used": 0,
    }


def test_adaptive_router_uses_dag_instead_of_fixed_chain(tmp_path: Path) -> None:
    plan_path = tmp_path / "execution-plan.json"
    plan(plan_path)
    artifacts = tmp_path / "artifacts"
    write_json(
        artifacts / "risk.json",
        {"risk_class": "low", "changed_areas": [], "high_risk_triggers": [], "protected_paths_touched": [], "protected_actions_required": [], "reasons": [], "autonomy_allowed": {}},
    )
    workflow = state(plan_path, ["issue-intake", "context-compiler"])

    route = decide_next_role(
        current_role="context-compiler",
        role_result={"status": "completed"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=workflow,
    )

    assert route["next_role"] == "implementation-agent"
    assert "Adaptive execution plan" in route["reason"]


def test_adaptive_soft_budget_exhaustion_continues_mandatory_next_node(tmp_path: Path) -> None:
    plan_path = tmp_path / "execution-plan.json"
    plan(plan_path, max_model_calls=1)
    artifacts = tmp_path / "artifacts"
    write_json(
        artifacts / "risk.json",
        {"risk_class": "low", "changed_areas": [], "high_risk_triggers": [], "protected_paths_touched": [], "protected_actions_required": [], "reasons": [], "autonomy_allowed": {}},
    )
    workflow = state(plan_path, ["issue-intake", "context-compiler", "implementation-agent"])

    route = decide_next_role(
        current_role="implementation-agent",
        role_result={"status": "completed"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=workflow,
    )

    assert route["next_role"] == "quality-runner"
    assert route["stop"] is False
    assert workflow["budget_action"]["action"] == "economy"
    assert workflow["budget_action"]["exhausted_dimensions"] == ["model_calls"]


def test_adaptive_router_ignores_historical_repair_counters(tmp_path: Path) -> None:
    plan_path = tmp_path / "execution-plan.json"
    plan(plan_path)
    artifacts = tmp_path / "artifacts"
    write_json(
        artifacts / "risk.json",
        {"risk_class": "low", "changed_areas": [], "high_risk_triggers": [], "protected_paths_touched": [], "protected_actions_required": [], "reasons": [], "autonomy_allowed": {}},
    )
    workflow = state(
        plan_path,
        ["issue-intake", "context-compiler", "implementation-agent", "quality-runner"],
    )
    workflow["loops"] = {
        **workflow["loops"],
        "quality_repair": {"iterations": 1},
    }
    workflow["last_route"] = {
        "next_role": "implementation-agent",
        "loop": None,
    }

    route = decide_next_role(
        current_role="implementation-agent",
        role_result={"status": "completed"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=workflow,
    )

    assert route["next_role"] == "security-agent"


def test_approved_hard_bound_window_runs_one_complete_review_repair_iteration(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "execution-plan.json"
    plan(plan_path, max_duration_seconds=1_200)
    artifacts = tmp_path / "artifacts"
    write_json(
        artifacts / "risk.json",
        {
            "risk_class": "low",
            "changed_areas": [],
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "protected_actions_required": [],
            "reasons": [],
            "autonomy_allowed": {},
        },
    )
    write_json(
        artifacts / "review.json",
        {
            "verdict": "broken",
            "status": "block",
            "blockers": ["F001: version endpoint returns 200 for a missing version"],
            "repair_required": True,
        },
    )
    workflow = state(
        plan_path,
        [
            "issue-intake",
            "context-compiler",
            "implementation-agent",
            "quality-runner",
            "security-agent",
            "reviewer",
        ],
    )
    workflow.update(
        {
            "elapsed_seconds": 1_656,
            "adaptive_budget_extensions": [
                {
                    "approval_id": "duration-extension",
                    "dimensions": ["elapsed_seconds"],
                    "baselines": {"elapsed_seconds": 1_656},
                }
            ],
            "approval_override": {
                "approval_id": "duration-extension",
                "gate": "reviewer",
                "scope": {
                    "actions": ["extend_execution_budget", "resume_workflow"],
                    "gate": "reviewer",
                },
            },
        }
    )

    repair = decide_next_role(
        current_role="reviewer",
        role_result={"status": "completed", "next_action": "repair"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=workflow,
    )

    assert repair["next_role"] == "implementation-agent"
    assert repair["stop"] is False
    assert "approval_override" not in workflow

    workflow["roles"].append(
        {
            "role": "implementation-agent",
            "llm_invoked": True,
            "result": {"status": "completed", "tokens_used": 1},
        }
    )
    workflow["role_count"] = len(workflow["roles"])
    workflow["elapsed_seconds"] = 1_700
    workflow["last_route"] = repair

    verify_repair = decide_next_role(
        current_role="implementation-agent",
        role_result={"status": "completed", "next_action": "continue"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=workflow,
    )

    assert verify_repair["next_role"] == "reviewer"
    assert verify_repair["stop"] is False
