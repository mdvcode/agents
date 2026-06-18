from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_artifacts.py"
SPEC = importlib.util.spec_from_file_location("validate_artifacts", MODULE_PATH)
assert SPEC is not None
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(validator)


def risk_payload(risk_class: str, **autonomy_overrides: bool) -> dict[str, Any]:
    publish_allowed = risk_class in {"low", "medium"}
    autonomy = {
        "patch": True,
        "commit": publish_allowed,
        "push": publish_allowed,
        "open_pr": publish_allowed,
        "update_pr": publish_allowed,
        "auto_merge": False,
        "deploy_staging": False,
        "deploy_production": False,
    }
    autonomy.update(autonomy_overrides)
    return {
        "risk_class": risk_class,
        "reasons": [],
        "changed_areas": [],
        "high_risk_triggers": [],
        "protected_paths_touched": [],
        "protected_actions_required": [],
        "autonomy_allowed": autonomy,
    }


def verdict_payload(**overrides: Any) -> dict[str, Any]:
    data = {
        "decision": "publish_pr",
        "execution_status": "planned",
        "task": "Task",
        "project_profile": "agent_workspace",
        "risk_class": "medium",
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
        "approval_required_before_publish": False,
        "approval_required_before_merge": True,
        "flowfox_visual_evidence": {
            "required": False,
            "provided": False,
            "items": [],
        },
        "reasoning_summary": [],
        "next_actions": [],
        "lessons_updated": False,
    }
    publication_result = overrides.pop("publication_result", None)
    data.update(overrides)
    if publication_result is not None:
        data["publication_result"].update(publication_result)
    return data


def test_valid_low_risk_invariants_pass() -> None:
    assert validator.validate_risk_invariants(risk_payload("low")) == []


def test_valid_medium_risk_invariants_pass() -> None:
    assert validator.validate_risk_invariants(risk_payload("medium")) == []


def test_valid_high_risk_invariants_pass() -> None:
    assert validator.validate_risk_invariants(risk_payload("high")) == []


def test_high_with_open_pr_true_fails() -> None:
    errors = validator.validate_risk_invariants(risk_payload("high", open_pr=True))
    assert any("open_pr=false" in error for error in errors)


def test_medium_with_commit_false_fails() -> None:
    errors = validator.validate_risk_invariants(risk_payload("medium", commit=False))
    assert any("commit=true" in error for error in errors)


def test_open_pr_verdict_without_pr_url_fails_when_pr_marked_created() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(
            publication_result={
                "commit_created": True,
                "branch_pushed": True,
                "pr_created_or_updated": True,
                "pr_url": "",
                "pr_state": "draft",
            }
        )
    )
    assert any("requires pr_url" in error for error in errors)


def test_ready_pr_with_checks_failed_fails() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(checks_passed=False, publication_result={"pr_state": "ready"})
    )
    assert any("pr_state=ready" in error for error in errors)


def test_failed_checks_with_created_pr_must_be_draft() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(
            checks_passed=False,
            publication_result={
                "commit_created": True,
                "branch_pushed": True,
                "pr_created_or_updated": True,
                "pr_url": "https://github.com/example/repo/pull/1",
                "pr_state": "ready",
            },
        )
    )
    assert any("require pr_state=draft" in error for error in errors)


def test_branch_pushed_without_commit_fails() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(publication_result={"branch_pushed": True})
    )
    assert any("branch_pushed=true requires commit_created=true" in error for error in errors)


def test_await_approval_requires_publish_approval_flag() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(decision="await_approval", approval_required_before_publish=False)
    )
    assert any("await_approval requires approval_required_before_publish=true" in error for error in errors)


def test_completed_publish_requires_pr_created_and_url() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(decision="publish_pr", execution_status="completed")
    )
    assert any("completed publish_pr requires pr_created_or_updated=true" in error for error in errors)
    assert any("completed publish_pr requires pr_url" in error for error in errors)


def test_high_risk_with_commit_created_fails() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(
            decision="await_approval",
            risk_class="high",
            approval_required_before_publish=True,
            publication_result={"commit_created": True},
        )
    )
    assert any("high risk must not create commits" in error for error in errors)


def test_missing_visual_evidence_ready_pr_fails() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(
            publication_result={"pr_state": "ready"},
            flowfox_visual_evidence={"required": True, "provided": False, "items": []},
        )
    )
    assert any("pr_state=ready requires required visual evidence" in error for error in errors)


def test_nested_publication_result_type_validation_fails() -> None:
    schema = validator.load_json(Path(__file__).resolve().parents[1] / "schemas" / "verdict.schema.json")
    errors = validator.validate_required(
        verdict_payload(publication_result={"commit_created": "yes"}),
        schema,
        "verdict.json",
    )
    assert any("publication_result.'commit_created' must be bool" in error for error in errors)


def test_nested_publication_result_enum_validation_fails() -> None:
    schema = validator.load_json(Path(__file__).resolve().parents[1] / "schemas" / "verdict.schema.json")
    errors = validator.validate_required(
        verdict_payload(publication_result={"pr_state": "banana"}),
        schema,
        "verdict.json",
    )
    assert any("publication_result.'pr_state' has invalid value" in error for error in errors)


def test_profile_required_commands_must_be_selected() -> None:
    profiles = {
        "profiles": {
            "agent_workspace": {
                "quality_commands": {"required": ["make check"]},
                "security_commands": {"required": ["make security"]},
            }
        }
    }
    errors = validator.validate_profile_command_selection(
        {
            "project_profile": "agent_workspace",
            "quality_commands_selected": ["make check"],
            "security_commands_selected": [],
        },
        profiles,
    )
    assert any("security_commands_selected missing required command 'make security'" in error for error in errors)


def test_profile_mismatch_across_artifacts_fails() -> None:
    errors = validator.validate_cross_artifact_invariants(
        {
            "project_profile": {"project_profile": "agent_workspace"},
            "quality": {"project_profile": "flowfox"},
            "verdict": {"project_profile": "agent_workspace", "risk_class": "medium"},
            "risk": {"risk_class": "medium"},
        }
    )
    assert any("project profile mismatch" in error for error in errors)


def test_invalid_yaml_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("version: [\n", encoding="utf-8")
    _, errors = validator.load_yaml(path, "bad.yaml")
    assert errors
    assert "invalid YAML" in errors[0]
