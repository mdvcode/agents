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
        "action": "open_pr",
        "task": "Task",
        "project_profile": "agent_workspace",
        "risk_class": "medium",
        "checks_attempted": True,
        "checks_passed": True,
        "blockers": [],
        "warnings": [],
        "high_risk_triggers": [],
        "protected_paths_touched": [],
        "commit_created": False,
        "branch_pushed": False,
        "pr_created_or_updated": False,
        "pr_url": "",
        "pr_state": "not_created",
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
    data.update(overrides)
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
        verdict_payload(pr_created_or_updated=True, pr_url="")
    )
    assert any("requires pr_url" in error for error in errors)


def test_ready_pr_with_checks_failed_fails() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(checks_passed=False, pr_state="ready")
    )
    assert any("pr_state=ready" in error for error in errors)


def test_failed_checks_with_created_pr_must_be_draft() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(
            checks_passed=False,
            pr_created_or_updated=True,
            pr_url="https://github.com/example/repo/pull/1",
            pr_state="ready",
        )
    )
    assert any("require pr_state=draft" in error for error in errors)


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
