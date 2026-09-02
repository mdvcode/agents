from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_router import decide_next_role  # noqa: E402
from security_approval import security_scope  # noqa: E402


REQUIRED = [
    "issue-intake",
    "context-compiler",
    "planner",
    "risk-classifier",
    "implementation-agent",
    "test-generator",
    "quality-runner",
    "security-agent",
    "reviewer",
    "orchestrator",
]


def artifact(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value), encoding="utf-8")


def completed_state(**extra: object) -> dict[str, object]:
    roles = [{"role": role, "result": {"status": "completed", "tokens_used": 1}} for role in REQUIRED]
    return {
        "run_id": "run-router",
        "workflow": "full_agent_workflow",
        "roles": roles,
        "completed_roles": REQUIRED,
        "role_count": len(roles),
        "tokens_used": len(roles),
        "loops": {
            "security_repair": {"iterations": 0},
            "quality_repair": {"iterations": 0},
            "review_repair": {"iterations": 0},
            "ci_repair": {"iterations": 0},
            "frontend_verification_repair": {"iterations": 0},
        },
        **extra,
    }


def setup_artifacts(tmp_path: Path, risk_class: str = "low", changed_areas: list[str] | None = None) -> Path:
    artifacts_dir = tmp_path / "artifacts"
    artifact(
        artifacts_dir / "risk.json",
        {
            "risk_class": risk_class,
            "reasons": [],
            "changed_areas": changed_areas or [],
            "changed_files": [],
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "protected_actions_required": [],
            "autonomy_allowed": {
                "patch": True,
                "commit": risk_class != "high",
                "push": risk_class != "high",
                "open_pr": risk_class != "high",
                "update_pr": risk_class != "high",
                "auto_merge": False,
                "deploy_staging": False,
                "deploy_production": False,
            },
        },
    )
    artifact(artifacts_dir / "issue.json", {})
    artifact(artifacts_dir / "plan.md", "# Plan\n")
    artifact(
        artifacts_dir / "project_profile.json",
        {
            "project_profile": "agent_workspace",
            "confidence": "high",
            "reasons": [],
            "matched_markers": [],
            "quality_commands_selected": [],
            "security_commands_selected": [],
            "frontend_evidence_required": False,
            "warnings": [],
        },
    )
    artifact(artifacts_dir / "implementation.json", {"changed_files": []})
    artifact(artifacts_dir / "test_plan.json", {"tests": [], "summary": "covered"})
    artifact(artifacts_dir / "test_result.json", {"status": "pass", "summary": "covered"})
    artifact(
        artifacts_dir / "quality.json",
        {
            "task": "test",
            "project_profile": "agent_workspace",
            "overall_status": "pass",
            "checks": [],
            "commands_attempted": [],
            "focused_tests_passed": True,
            "repository_checks_passed": True,
            "coverage": "not measured",
            "warnings": [],
        },
    )
    artifact(
        artifacts_dir / "security.json",
        {
            "verdict": "works",
            "expected": [],
            "observed": [],
            "evidence": [],
            "blockers": [],
            "repair_required": False,
            "status": "pass",
            "highest_severity": "none",
            "project_profile": "agent_workspace",
            "findings": [],
            "blocker_ids": [],
            "secret_findings": [],
            "commands_attempted": [],
            "warnings": [],
        },
    )
    artifact(
        artifacts_dir / "review.json",
        {
            "verdict": "works",
            "expected": [],
            "observed": [],
            "evidence": [],
            "blockers": [],
            "repair_required": False,
            "status": "pass",
            "project_profile": "agent_workspace",
            "findings": [],
            "blocker_ids": [],
            "policy_violations": [],
            "known_lesson_conflicts": [],
            "warnings": [],
        },
    )
    artifact(
        artifacts_dir / "verdict.json",
        {
            "decision": "publish_pr",
            "execution_status": "planned",
            "task": "test",
            "project_profile": "agent_workspace",
            "risk_class": risk_class,
            "checks_attempted": True,
            "checks_passed": True,
            "blockers": [],
            "warnings": [],
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "publication_result": {
                "commit_created": False,
                "branch_pushed": False,
                "pr_created_or_updated": False,
                "pr_url": "",
                "pr_state": "not_created",
            },
            "visual_evidence": {"required": False, "provided": False, "items": []},
            "approval_required_before_publish": False,
            "approval_required_before_merge": True,
            "reasoning_summary": [],
            "next_actions": [],
            "lessons_updated": False,
        },
    )
    context = artifacts_dir.parent / "context"
    artifact(context / "planner.json", {})
    return artifacts_dir


def route(tmp_path: Path, state: dict[str, object], current_role: str, result: dict[str, object] | None = None) -> dict[str, object]:
    artifacts_dir = tmp_path / "artifacts"
    return decide_next_role(
        current_role=current_role,
        role_result=result or {"status": "completed", "next_action": "continue"},
        run_dir=tmp_path,
        artifacts_dir=artifacts_dir,
        workflow_state=state,
    )


def test_high_risk_routes_to_approval_and_cannot_publish(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path, "high")
    result = decide_next_role(
        current_role="risk-classifier",
        role_result={"status": "completed", "next_action": "publication"},
        run_dir=tmp_path,
        artifacts_dir=artifacts_dir,
        workflow_state=completed_state(),
    )
    assert result["next_role"] == "approval-gate"
    assert result["stop"] is True
    assert result["publication_allowed"] is False


def test_fast_context_skips_planner_and_risk_classifier(tmp_path: Path) -> None:
    setup_artifacts(tmp_path)
    state = completed_state(effective_mode="fast", roles=[], completed_roles=[])

    result = route(tmp_path, state, "context-compiler")

    assert result["next_role"] == "implementation-agent"


def test_fast_bounded_implementation_routes_directly_to_quality(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "implementation.json",
        {"changed_files": ["styles/site.css"], "risk_changed": False},
    )
    state = completed_state(effective_mode="fast", roles=[], completed_roles=[])

    result = route(tmp_path, state, "implementation-agent")

    assert result["next_role"] == "quality-runner"
    assert state["effective_mode"] == "fast"


def test_fast_large_diff_escalates_to_full_without_reimplementing(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "implementation.json",
        {"changed_files": [f"src/file-{index}.py" for index in range(6)], "risk_changed": False},
    )
    state = completed_state(effective_mode="fast", roles=[], completed_roles=[])

    result = route(tmp_path, state, "implementation-agent")

    assert result["next_role"] == "planner"
    assert state["effective_mode"] == "full"
    assert state["fast_escalation_reasons"]

    state["roles"] = [{"role": "implementation-agent", "result": {"status": "completed"}}]
    risk_route = route(tmp_path, state, "risk-classifier")
    assert risk_route["next_role"] == "test-generator"


def test_fast_non_code_escalation_skips_test_generator(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "implementation.json",
        {"changed_files": ["docs/guide.md"], "risk_changed": True},
    )
    state = completed_state(effective_mode="fast", roles=[], completed_roles=[])

    result = route(tmp_path, state, "implementation-agent")

    assert result["next_role"] == "planner"
    state["roles"] = [
        {"role": "implementation-agent", "result": {"status": "completed"}}
    ]
    risk_route = route(tmp_path, state, "risk-classifier")
    assert risk_route["next_role"] == "quality-runner"


def test_environmental_verifier_failure_does_not_repeat_implementation(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "semantic_conflict.json",
        {
            "verdict": "broken",
            "blockers": ["Browser verification is unavailable because dependencies are missing."],
            "repair_required": True,
        },
    )
    state = completed_state()

    result = route(tmp_path, state, "semantic-conflict-agent")

    assert result["next_role"] == "approval-gate"
    assert "will not be repeated" in result["reason"]


def test_mixed_environment_and_code_verifier_blockers_start_repair(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "architecture_consistency.json",
        {
            "verdict": "broken",
            "blockers": [
                "P2: terminal cleanup rejection overrides an authoritative done event.",
                "Browser verification is unavailable because dependencies are missing.",
            ],
            "repair_required": True,
        },
    )
    state = completed_state()

    result = route(tmp_path, state, "architecture-consistency-agent")

    assert result["next_role"] == "implementation-agent"
    assert result["stop"] is False
    assert result["loop"]["name"] == "review_repair"


def test_environmental_verifier_approval_advances_without_reprompting(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    semantic = {
        "verdict": "broken",
        "blockers": ["Browser verification is unavailable because dependencies are missing."],
        "repair_required": True,
    }
    artifact(artifacts_dir / "semantic_conflict.json", semantic)
    state = completed_state(
        approval_grants=[
            {
                "approval_id": "accepted-unavailable-verification",
                "gate": "semantic-conflict-agent",
                "scope": {
                    "actions": ["accept_unavailable_verification", "resume_workflow"],
                    "verifier_fingerprint": hashlib.sha256(
                        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest(),
                },
                "reason": "semantic-conflict-agent could not verify the environment",
            }
        ],
    )

    result = route(tmp_path, state, "semantic-conflict-agent")

    assert result["next_role"] == "reviewer"
    assert result["stop"] is False
    assert any("publication must remain draft" in item for item in result["warnings"])


def test_active_verifier_acceptance_rebinds_to_rerun_artifact(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    review = {
        "verdict": "works",
        "status": "pass",
        "blockers": [],
        "warnings": ["Browser verification is unavailable."],
    }
    artifact(artifacts_dir / "review.json", review)
    scope = {
        "actions": ["accept_unavailable_verification", "resume_workflow"],
        "verifier_fingerprint": "previous-review-fingerprint",
    }
    state = completed_state(
        approval_override={
            "approval_id": "active-reviewer-acceptance",
            "gate": "reviewer",
            "scope": scope.copy(),
        },
        approval_grants=[
            {
                "approval_id": "active-reviewer-acceptance",
                "gate": "reviewer",
                "scope": scope.copy(),
            }
        ],
    )

    result = route(tmp_path, state, "reviewer")

    current_fingerprint = hashlib.sha256(
        json.dumps(review, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert result["next_role"] == "orchestrator"
    assert result["stop"] is False
    assert (
        state["approval_grants"][0]["scope"]["verifier_fingerprint"]
        == current_fingerprint
    )
    assert any("publication must remain draft" in item for item in result["warnings"])


def test_active_verifier_acceptance_does_not_cover_code_blockers(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "review.json",
        {
            "verdict": "broken",
            "status": "block",
            "blockers": [
                "P2: refresh admits stale response state.",
                "Browser verification is unavailable.",
            ],
            "repair_required": True,
        },
    )
    scope = {
        "actions": ["accept_unavailable_verification", "resume_workflow"],
        "verifier_fingerprint": "previous-review-fingerprint",
    }
    state = completed_state(
        approval_override={
            "approval_id": "active-reviewer-acceptance",
            "gate": "reviewer",
            "scope": scope.copy(),
        },
        approval_grants=[
            {
                "approval_id": "active-reviewer-acceptance",
                "gate": "reviewer",
                "scope": scope.copy(),
            }
        ],
    )

    result = route(tmp_path, state, "reviewer")

    assert result["next_role"] == "implementation-agent"
    assert result["stop"] is False
    assert (
        state["approval_grants"][0]["scope"]["verifier_fingerprint"]
        == "previous-review-fingerprint"
    )


def test_legacy_environmental_verifier_approval_advances_current_run(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "semantic_conflict.json",
        {
            "verdict": "broken",
            "blockers": ["Browser verification is unavailable because dependencies are missing."],
            "repair_required": True,
        },
    )
    state = completed_state(
        approval_grants=[
            {
                "approval_id": "legacy-acceptance",
                "gate": "semantic-conflict-agent",
                "scope": {"actions": ["resume_workflow"]},
                "reason": "semantic-conflict-agent could not verify the environment",
            }
        ],
    )

    result = route(tmp_path, state, "semantic-conflict-agent")

    assert result["next_role"] == "reviewer"
    assert result["stop"] is False


def test_legacy_reviewer_approval_advances_unavailable_review(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "review.json",
        {
            "verdict": "unavailable",
            "status": "block",
            "blockers": ["Browser save and reload evidence is unavailable."],
        },
    )
    state = completed_state(
        approval_grants=[
            {
                "approval_id": "legacy-reviewer-acceptance",
                "gate": "reviewer",
                "scope": {"actions": ["resume_workflow"]},
                "reason": "Workflow blockers are present; execution is awaiting approval.",
            }
        ],
    )

    result = route(tmp_path, state, "reviewer")

    assert result["next_role"] == "orchestrator"
    assert result["stop"] is False
    assert any("publication must remain draft" in item for item in result["warnings"])


def test_orchestrator_local_complete_finishes_without_publication_gate(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "verdict.json",
        {
            "decision": "local_complete",
            "execution_status": "completed",
            "checks_passed": True,
            "blockers": [],
            "warnings": ["Publication was not requested."],
        },
    )
    state = completed_state()

    result = route(tmp_path, state, "orchestrator")

    assert result["next_role"] == ""
    assert result["stop"] is False
    assert result["publication_allowed"] is False
    assert result["warnings"] == ["Publication was not requested."]


def test_technical_publication_failure_is_blocked_not_approval(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    state = completed_state()

    result = decide_next_role(
        current_role="publication",
        role_result={
            "status": "blocked",
            "next_action": "blocked",
            "summary": "Publication executor blocked or failed.",
            "blockers": ["ModuleNotFoundError: ai_harness"],
        },
        run_dir=tmp_path,
        artifacts_dir=artifacts_dir,
        workflow_state=state,
    )

    assert result["next_role"] == "blocked"
    assert result["stop"] is True
    assert result["reason"] == "Publication executor blocked or failed."


def test_scoped_high_risk_grant_allows_patch_but_not_publication(tmp_path: Path) -> None:
    setup_artifacts(tmp_path, "high")
    state = completed_state(
        approval_grants=[
            {
                "approval_id": "approved-patch",
                "gate": "risk-classifier",
                "scope": {
                    "actions": ["resume_workflow", "patch_high_risk"],
                    "paths": [],
                    "gate": "risk-classifier",
                    "risk_class": "high",
                },
            }
        ]
    )

    implementation = route(tmp_path, state, "risk-classifier")
    publication = route(tmp_path, state, "orchestrator")

    assert implementation["next_role"] == "implementation-agent"
    assert publication["next_role"] == "blocked"
    assert publication["stop"] is True
    assert publication["publication_allowed"] is False


def test_low_and_medium_risk_reach_publication_prepare_after_required_gates(tmp_path: Path) -> None:
    for risk_class in ("low", "medium"):
        case = tmp_path / risk_class
        artifacts_dir = setup_artifacts(case, risk_class)
        result = decide_next_role(
            current_role="orchestrator",
            role_result={"status": "completed", "next_action": "publication"},
            run_dir=case,
            artifacts_dir=artifacts_dir,
            workflow_state=completed_state(),
        )
        assert result["next_role"] == "publication-prepare"
        assert result["publication_allowed"] is True


def test_planner_advisory_publication_cannot_skip_risk_gate(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    state = {"roles": [], "loops": {}}
    result = decide_next_role(
        current_role="planner",
        role_result={"status": "completed", "next_action": "publication"},
        run_dir=tmp_path,
        artifacts_dir=artifacts_dir,
        workflow_state=state,
    )
    assert result["next_role"] == "risk-classifier"
    assert result["publication_allowed"] is False
    assert any("advisory" in warning for warning in result["warnings"])


def test_ui_changes_require_frontend_qa(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path, changed_areas=["ui"])
    result = route(tmp_path, completed_state(), "security-agent")
    assert result["next_role"] == "frontend-qa-agent"


def test_non_ui_changes_skip_frontend_qa(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    result = route(tmp_path, completed_state(), "security-agent")
    assert result["next_role"] == "reviewer"


def test_small_low_risk_code_change_skips_optional_deep_checks(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(artifacts_dir / "risk.json", {"risk_class": "low", "changed_files": ["src/service.py"]})
    state = completed_state(changed_files=["src/service.py"])
    result = route(tmp_path, state, "security-agent")
    assert result["next_role"] == "reviewer"


def test_structural_medium_risk_code_change_enables_deep_checks(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(artifacts_dir / "risk.json", {"risk_class": "medium", "changed_files": ["src/schema.py"]})
    state = completed_state(changed_files=["src/schema.py"])
    architecture = route(tmp_path, state, "security-agent")
    assert architecture["next_role"] == "architecture-consistency-agent"
    semantic = route(tmp_path, state, "architecture-consistency-agent")
    assert semantic["next_role"] == "semantic-conflict-agent"


def test_quality_failure_starts_bounded_repair(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(artifacts_dir / "quality.json", {"overall_status": "fail", "failed_command": "make check"})
    state = completed_state(diff_hash="diff-1")
    result = route(tmp_path, state, "quality-runner", {"status": "completed", "next_action": "publication"})
    assert result["next_role"] == "implementation-agent"
    assert result["loop"]["iteration"] == 1
    assert result["loop"]["progress_detected"] is True


def test_role_question_stops_before_quality_repair_loop(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(artifacts_dir / "quality.json", {"overall_status": "fail", "failed_command": "make check"})
    state = completed_state(diff_hash="diff-1")

    result = route(
        tmp_path,
        state,
        "quality-runner",
        {
            "status": "awaiting_approval",
            "next_action": "awaiting_approval",
            "summary": "Which database service should run the integration checks?",
            "blockers": ["Choose the local or staging database."],
        },
    )

    assert result["next_role"] == "approval-gate"
    assert result["stop"] is True
    assert result["reason"] == "Which database service should run the integration checks?"
    assert state["loops"]["quality_repair"]["iterations"] == 0  # type: ignore[index]


def test_review_blocker_starts_review_repair(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "review.json",
        {
            "verdict": "broken",
            "expected": [],
            "observed": [],
            "evidence": [],
            "blockers": ["R1"],
            "repair_required": True,
            "status": "block",
            "project_profile": "agent_workspace",
            "findings": ["R1"],
            "blocker_ids": ["R1"],
            "policy_violations": [],
            "known_lesson_conflicts": [],
            "warnings": [],
        },
    )
    state = completed_state(review_status="block")
    result = route(tmp_path, state, "reviewer", {"status": "completed", "next_action": "publication", "blockers": ["R1"]})
    assert result["next_role"] == "implementation-agent"
    assert result["loop"]["name"] == "review_repair"


def test_exhausted_review_repair_requests_one_scoped_approval(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    review = json.loads((artifacts_dir / "review.json").read_text(encoding="utf-8"))
    review.update(
        {
            "verdict": "broken",
            "status": "block",
            "blockers": ["REV-LOOP"],
            "blocker_ids": ["REV-LOOP"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "review.json", review)
    state = completed_state(review_status="block")
    state["loops"]["review_repair"]["iterations"] = 2  # type: ignore[index]

    result = route(tmp_path, state, "reviewer")

    assert result["next_role"] == "approval-gate"
    assert result["loop"]["iteration"] == 3
    assert state["loops"]["review_repair"]["iterations"] == 3  # type: ignore[index]


def test_consumed_repair_approval_cannot_prompt_again(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    review = json.loads((artifacts_dir / "review.json").read_text(encoding="utf-8"))
    review.update(
        {
            "verdict": "broken",
            "status": "block",
            "blockers": ["REV-LOOP"],
            "blocker_ids": ["REV-LOOP"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "review.json", review)
    state = completed_state(
        review_status="block",
        approval_override={
            "approval_id": "review-repair-approval",
            "gate": "reviewer",
            "scope": {"actions": ["resume_workflow"], "gate": "reviewer"},
        },
    )
    state["loops"]["review_repair"]["iterations"] = 11  # type: ignore[index]

    result = route(tmp_path, state, "reviewer")

    assert result["next_role"] == "blocked"
    assert result["stop"] is True
    assert "still unresolved" in result["reason"]
    assert result["loop"]["iteration"] == 3
    assert state["loops"]["review_repair"]["iterations"] == 3  # type: ignore[index]


def test_consumed_one_time_repair_extension_starts_one_more_repair(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    review = json.loads((artifacts_dir / "review.json").read_text(encoding="utf-8"))
    review.update(
        {
            "verdict": "broken",
            "status": "block",
            "blockers": ["REV-LOOP"],
            "blocker_ids": ["REV-LOOP"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "review.json", review)
    state = completed_state(
        review_status="block",
        approval_override={
            "approval_id": "review-repair-extension",
            "gate": "reviewer",
            "scope": {
                "actions": ["extend_repair_budget", "resume_workflow"],
                "gate": "reviewer",
            },
        },
    )
    state["loops"]["review_repair"] = {"iterations": 3, "extensions_used": 0}  # type: ignore[index]

    result = route(tmp_path, state, "reviewer")

    assert result["next_role"] == "implementation-agent"
    assert result["stop"] is False
    assert "one-time approved repair extension" in result["reason"]
    assert state["loops"]["review_repair"]["extensions_used"] == 1  # type: ignore[index]


def test_second_repair_extension_is_not_reusable(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    review = json.loads((artifacts_dir / "review.json").read_text(encoding="utf-8"))
    review.update(
        {
            "verdict": "broken",
            "status": "block",
            "blockers": ["REV-LOOP"],
            "blocker_ids": ["REV-LOOP"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "review.json", review)
    state = completed_state(
        review_status="block",
        approval_override={
            "approval_id": "second-review-repair-extension",
            "gate": "reviewer",
            "scope": {
                "actions": ["extend_repair_budget", "resume_workflow"],
                "gate": "reviewer",
            },
        },
    )
    state["loops"]["review_repair"] = {"iterations": 3, "extensions_used": 1}  # type: ignore[index]

    result = route(tmp_path, state, "reviewer")

    assert result["next_role"] == "blocked"
    assert result["stop"] is True
    assert state["loops"]["review_repair"]["extensions_used"] == 1  # type: ignore[index]


def test_critical_security_finding_blocks_workflow(tmp_path: Path) -> None:
    setup_artifacts(tmp_path)
    state = completed_state(security_blockers_present=True)
    result = route(tmp_path, state, "security-agent")
    assert result["next_role"] == "blocked"
    assert result["stop"] is True


def test_critical_security_finding_cannot_be_human_accepted(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    security = json.loads((artifacts_dir / "security.json").read_text(encoding="utf-8"))
    security.update(
        {
            "verdict": "broken",
            "status": "fail",
            "highest_severity": "critical",
            "findings": [{"id": "SEC-CRITICAL", "severity": "critical"}],
            "blockers": ["SEC-CRITICAL"],
            "blocker_ids": ["SEC-CRITICAL"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "security.json", security)
    scope = {
        "actions": ["accept_security_finding", "resume_workflow"],
        "gate": "security-agent",
        **security_scope(security),
    }
    state = completed_state(
        approval_grants=[
            {"approval_id": "critical", "gate": "security-agent", "scope": scope}
        ]
    )

    result = route(tmp_path, state, "security-agent")

    assert result["next_role"] == "blocked"
    assert result["stop"] is True


def test_medium_security_finding_routes_to_approval(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    security = json.loads((artifacts_dir / "security.json").read_text(encoding="utf-8"))
    security.update(
        {
            "verdict": "broken",
            "status": "warn",
            "highest_severity": "medium",
            "findings": [{"id": "SEC-MEDIUM", "severity": "medium"}],
            "blockers": ["SEC-MEDIUM"],
            "blocker_ids": ["SEC-MEDIUM"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "security.json", security)

    result = route(tmp_path, completed_state(), "security-agent")

    assert result["next_role"] == "approval-gate"
    assert result["stop"] is True


def test_approved_medium_security_finding_routes_to_implementation(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    security = json.loads((artifacts_dir / "security.json").read_text(encoding="utf-8"))
    security.update(
        {
            "verdict": "broken",
            "status": "warn",
            "highest_severity": "medium",
            "findings": [{"id": "SEC-MEDIUM", "severity": "medium"}],
            "blockers": ["SEC-MEDIUM"],
            "blocker_ids": ["SEC-MEDIUM"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "security.json", security)
    state = completed_state(
        approval_override={
            "approval_id": "security-repair-approval",
            "gate": "security-agent",
            "scope": {"actions": ["resume_workflow"], "gate": "security-agent"},
        }
    )

    result = route(tmp_path, state, "security-agent")

    assert result["next_role"] == "implementation-agent"
    assert result["stop"] is False
    assert result["loop"]["name"] == "security_repair"


def test_accepted_medium_security_finding_continues_without_repair(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    security = json.loads((artifacts_dir / "security.json").read_text(encoding="utf-8"))
    security.update(
        {
            "verdict": "broken",
            "status": "warn",
            "highest_severity": "medium",
            "findings": [
                {
                    "id": "SEC-MEDIUM",
                    "severity": "medium",
                    "status": "confirmed",
                    "category": "debug_logging",
                    "scope": "pre-existing",
                }
            ],
            "blockers": ["SEC-MEDIUM requires acceptance"],
            "blocker_ids": ["SEC-MEDIUM"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "security.json", security)
    scope = {
        "actions": ["accept_security_finding", "resume_workflow"],
        "gate": "security-agent",
        **security_scope(security),
    }
    state = completed_state(
        approval_override={
            "approval_id": "security-acceptance",
            "gate": "security-agent",
            "scope": scope,
        },
        approval_grants=[
            {
                "approval_id": "security-acceptance",
                "gate": "security-agent",
                "scope": scope,
            }
        ],
    )

    result = route(
        tmp_path,
        state,
        "security-agent",
        {
            "status": "completed",
            "next_action": "repair",
            "blockers": ["SEC-MEDIUM requires acceptance"],
        },
    )

    assert result["next_role"] == "reviewer"
    assert result["stop"] is False
    assert state["loops"]["security_repair"]["iterations"] == 0


def test_legacy_consumed_security_grant_remains_valid_for_existing_run(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    security = json.loads((artifacts_dir / "security.json").read_text(encoding="utf-8"))
    security.update(
        {
            "verdict": "broken",
            "status": "fail",
            "highest_severity": "medium",
            "findings": [{"id": "SEC-AUTH-001", "severity": "medium"}],
            "blockers": ["SEC-AUTH-001 requires acceptance"],
            "blocker_ids": ["SEC-AUTH-001"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "security.json", security)
    state = completed_state(
        approval_grants=[
            {
                "approval_id": "legacy-security-acceptance",
                "gate": "security-agent",
                "scope": {
                    "actions": ["accept_security_finding", "resume_workflow"],
                    "gate": "security-agent",
                },
            }
        ]
    )

    result = route(tmp_path, state, "security-agent")

    assert result["next_role"] == "reviewer"
    assert result["stop"] is False


def test_changed_security_finding_invalidates_scoped_acceptance(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    security = json.loads((artifacts_dir / "security.json").read_text(encoding="utf-8"))
    security.update(
        {
            "verdict": "broken",
            "status": "warn",
            "highest_severity": "medium",
            "findings": [{"id": "SEC-OLD", "severity": "medium"}],
            "blockers": ["SEC-OLD"],
            "blocker_ids": ["SEC-OLD"],
            "repair_required": True,
        }
    )
    old_scope = {
        "actions": ["accept_security_finding", "resume_workflow"],
        "gate": "security-agent",
        **security_scope(security),
    }
    security["findings"] = [{"id": "SEC-NEW", "severity": "high"}]
    security["blockers"] = ["SEC-NEW"]
    security["blocker_ids"] = ["SEC-NEW"]
    security["highest_severity"] = "high"
    artifact(artifacts_dir / "security.json", security)
    state = completed_state(
        approval_override={
            "approval_id": "stale-security-acceptance",
            "gate": "security-agent",
            "scope": old_scope,
        },
        approval_grants=[
            {
                "approval_id": "stale-security-acceptance",
                "gate": "security-agent",
                "scope": old_scope,
            }
        ],
    )

    result = route(tmp_path, state, "security-agent")

    assert result["next_role"] == "approval-gate"
    assert result["stop"] is True


def test_accepted_security_artifact_is_valid_required_gate(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    security = json.loads((artifacts_dir / "security.json").read_text(encoding="utf-8"))
    security.update(
        {
            "verdict": "broken",
            "status": "warn",
            "highest_severity": "medium",
            "findings": [{"id": "SEC-MEDIUM", "severity": "medium"}],
            "blockers": ["SEC-MEDIUM"],
            "blocker_ids": ["SEC-MEDIUM"],
            "repair_required": True,
        }
    )
    artifact(artifacts_dir / "security.json", security)
    scope = {
        "actions": ["accept_security_finding", "resume_workflow"],
        "gate": "security-agent",
        **security_scope(security),
    }
    state = completed_state(
        approval_grants=[
            {"approval_id": "accepted", "gate": "security-agent", "scope": scope}
        ]
    )

    result = route(tmp_path, state, "orchestrator")

    assert result["next_role"] == "publication-prepare"


def test_missing_frontend_evidence_allows_draft_only_publication(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path, changed_areas=["ui"])
    artifact(
        artifacts_dir / "frontend_qa.json",
        {
            "verdict": "unavailable",
            "expected": [],
            "observed": [],
            "evidence": [],
            "blockers": ["browser unavailable"],
            "repair_required": False,
            "evidence_required": True,
            "evidence_collected": False,
            "screenshots": [],
            "console_errors": [],
            "network_errors": [],
            "local_url": "",
            "dev_server": {},
            "next_action": "continue",
        },
    )
    state = completed_state()
    state["roles"].append({"role": "frontend-qa-agent", "result": {"status": "completed"}})  # type: ignore[union-attr]
    result = route(tmp_path, state, "publication-prepare")
    assert result["next_role"] == "publication"
    assert result["publication_allowed"] is True
    assert any("draft" in warning for warning in result["warnings"])


def test_broken_frontend_verification_starts_bounded_repair(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path, changed_areas=["ui"])
    artifact(
        artifacts_dir / "frontend_qa.json",
        {
            "verdict": "broken",
            "expected": ["button submits"],
            "observed": ["button throws"],
            "evidence": ["screenshot.png"],
            "blockers": ["UI-1"],
            "repair_required": True,
        },
    )
    state = completed_state(diff_hash="ui-diff")

    result = route(tmp_path, state, "frontend-qa-agent")

    assert result["next_role"] == "implementation-agent"
    assert result["loop"]["name"] == "frontend_verification_repair"
    assert result["loop"]["diff_fingerprint"] == "ui-diff"


def test_loop_token_budget_continues_repair_in_economy(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(artifacts_dir / "quality.json", {"overall_status": "fail", "failed_command": "pytest"})
    state = completed_state(diff_hash="changed", tokens_used=70000)
    state["loops"]["quality_repair"] = {"iterations": 0, "tokens_at_start": 0, "elapsed_at_start": 0}  # type: ignore[index]

    result = route(tmp_path, state, "quality-runner")

    assert result["next_role"] == "implementation-agent"
    assert result["stop"] is False
    assert state["budget_action"]["action"] == "economy"
    assert any("soft repair token ceiling" in warning for warning in result["warnings"])


def test_loop_time_budget_routes_to_approval(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(artifacts_dir / "quality.json", {"overall_status": "fail", "failed_command": "pytest"})
    state = completed_state(diff_hash="changed", elapsed_seconds=2000)
    state["loops"]["quality_repair"] = {"iterations": 0, "tokens_at_start": 0, "elapsed_at_start": 0}  # type: ignore[index]

    result = route(tmp_path, state, "quality-runner")

    assert result["next_role"] == "approval-gate"
    assert any("seconds" in warning for warning in result["warnings"])


def test_publication_remains_unreachable_until_required_gates_exist(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    result = route(tmp_path, {"roles": [], "loops": []}, "orchestrator")
    assert result["next_role"] == "issue-intake"
    assert result["publication_allowed"] is False


def test_invalid_required_artifact_keeps_publication_unreachable(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(artifacts_dir / "quality.json", {})
    result = route(tmp_path, completed_state(), "orchestrator")
    assert result["next_role"] == "quality-runner"
    assert result["publication_allowed"] is False


def test_role_budget_remains_a_hard_approval_bound(tmp_path: Path) -> None:
    setup_artifacts(tmp_path)
    result = route(tmp_path, completed_state(role_count=41), "reviewer")

    assert result["next_role"] == "approval-gate"
    assert result["stop"] is True


def test_workflow_token_budget_continues_in_economy(tmp_path: Path) -> None:
    setup_artifacts(tmp_path)
    state = completed_state(tokens_used=1500001)

    result = route(tmp_path, state, "reviewer")

    assert result["next_role"] == "orchestrator"
    assert result["stop"] is False
    assert state["budget_action"]["action"] == "economy"
    assert any("soft max_tokens" in warning for warning in result["warnings"])


def test_budget_approval_override_allows_checkpoint_to_continue(tmp_path: Path) -> None:
    setup_artifacts(tmp_path)
    state = completed_state(
        tokens_used=300001,
        approval_override={
            "approval_id": "budget-approval",
            "gate": "quality-runner",
            "scope": {"actions": ["resume_workflow"], "gate": "quality-runner"},
        },
    )

    result = route(tmp_path, state, "quality-runner")

    assert result["next_role"] == "security-agent"
    assert result["stop"] is False


def test_budget_approval_grant_remains_valid_after_checkpoint(tmp_path: Path) -> None:
    setup_artifacts(tmp_path)
    state = completed_state(
        tokens_used=300001,
        approval_grants=[
            {
                "approval_id": "budget-approval",
                "gate": "quality-runner",
                "scope": {"actions": ["resume_workflow"], "gate": "quality-runner"},
                "reason": "Workflow budget exceeded; execution is awaiting approval.",
            }
        ],
    )

    result = route(tmp_path, state, "security-agent")

    assert result["next_role"] == "reviewer"
    assert result["stop"] is False


def test_workflow_blockers_prevent_publication(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    result = route(tmp_path, completed_state(blockers=["orchestrator blocker"]), "orchestrator")
    assert result["next_role"] == "approval-gate"
    assert result["publication_allowed"] is False


def test_resumed_role_ignores_historical_approval_and_superseded_blockers(tmp_path: Path) -> None:
    setup_artifacts(tmp_path)
    state = completed_state()
    state["roles"].extend(
        [
            {
                "role": "quality-runner",
                "result": {"status": "failed", "blockers": ["old quality failure"]},
            },
            {
                "role": "approval-gate",
                "result": {"status": "awaiting_approval", "blockers": ["old budget stop"]},
            },
            {
                "role": "quality-runner",
                "result": {"status": "completed", "blockers": []},
            },
            {
                "role": "implementation-agent",
                "result": {"status": "completed", "blockers": []},
            },
        ]
    )

    implementation = route(tmp_path, state, "implementation-agent")
    test_generation = route(tmp_path, state, "test-generator")

    assert implementation["next_role"] == "quality-runner"
    assert implementation["stop"] is False
    assert test_generation["next_role"] == "quality-runner"
    assert test_generation["stop"] is False


def test_successful_verifier_artifact_supersedes_historical_role_blockers(tmp_path: Path) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "semantic_conflict.json",
        {
            "verdict": "works",
            "blockers": [],
            "repair_required": False,
        },
    )
    state = completed_state()
    state["roles"].extend(
        [
            {
                "role": "semantic-conflict-agent",
                "result": {
                    "status": "completed",
                    "blockers": ["old missing browser evidence"],
                },
            },
            {
                "role": "implementation-agent",
                "result": {"status": "completed", "blockers": []},
            },
        ]
    )

    result = route(tmp_path, state, "implementation-agent")

    assert result["next_role"] == "quality-runner"
    assert result["stop"] is False


def test_implementation_in_review_repair_defers_old_verifier_blockers_to_rerun(
    tmp_path: Path,
) -> None:
    artifacts_dir = setup_artifacts(tmp_path)
    artifact(
        artifacts_dir / "architecture_consistency.json",
        {
            "verdict": "broken",
            "blockers": ["P2: terminal cleanup rejection overrides done handling."],
            "repair_required": True,
        },
    )
    state = completed_state()
    state["loops"]["review_repair"]["iterations"] = 1
    state["roles"].extend(
        [
            {
                "role": "architecture-consistency-agent",
                "result": {
                    "status": "completed",
                    "blockers": ["P2: terminal cleanup rejection overrides done handling."],
                },
            },
            {
                "role": "implementation-agent",
                "result": {"status": "completed", "blockers": []},
            },
        ]
    )

    result = route(tmp_path, state, "implementation-agent")

    assert result["next_role"] == "quality-runner"
    assert result["stop"] is False
