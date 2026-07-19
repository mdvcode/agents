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
        "visual_evidence": {
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


def test_issue_intake_contract_is_explicitly_non_llm_harness_stage() -> None:
    contracts, errors = validator.load_yaml(
        Path(__file__).resolve().parents[1] / ".agent-role-contracts.yaml",
        ".agent-role-contracts.yaml",
    )

    assert errors == []
    assert validator.validate_role_execution_contracts(contracts) == []
    contracts["roles"]["issue-intake"]["llm_invocation"] = True
    assert validator.validate_role_execution_contracts(contracts) == [
        ".agent-role-contracts.yaml: issue-intake must set llm_invocation=false"
    ]


def test_step2_runtime_config_allows_only_local_codex_cli_without_router() -> None:
    config = {
        "version": 1,
        "runtime": {
            "provider": "codex-cli",
            "executor_command": "python3 scripts/adapters/codex_cli_executor.py",
            "transport": "local_subscription",
            "api_required": False,
            "model_router": False,
        },
    }

    assert validator.validate_runtime_config_data(config) == []
    config["runtime"]["api_required"] = True
    config["runtime"]["model_router"] = True
    errors = validator.validate_runtime_config_data(config)
    assert any("must not require an API" in error for error in errors)
    assert any("model_router is forbidden" in error for error in errors)


def policy_payload(branch_prefixes: list[str] | None = None) -> dict[str, Any]:
    allowed = branch_prefixes or ["feat/", "fix/", "issue/", "tast/"]
    publication = {
        "allowed_branch_prefixes": allowed,
        "low": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        "medium": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        "high": {"commit": False, "push": False, "open_pr": False, "update_pr": False},
    }
    return {
        "version": 1,
        "risk_classes": {
            "low": {
                "patch": True,
                "commit": True,
                "push": True,
                "open_pr": True,
                "update_pr": True,
                "require_human_approval": False,
                "auto_merge": False,
                "deploy_staging": False,
                "deploy_production": False,
            },
            "medium": {
                "patch": True,
                "commit": True,
                "push": True,
                "open_pr": True,
                "update_pr": True,
                "require_human_approval": False,
                "auto_merge": False,
                "deploy_staging": False,
                "deploy_production": False,
            },
            "high": {
                "patch": True,
                "commit": False,
                "push": False,
                "open_pr": False,
                "update_pr": False,
                "require_human_approval": True,
                "auto_merge": False,
                "deploy_staging": False,
                "deploy_production": False,
            },
        },
        "projects": {
            "nextjs_web": {
                "publication": publication,
                "require_visual_evidence_for": ["ui"],
                "visual_evidence_policy": {
                    "ready_pr_requires_evidence": True,
                    "missing_evidence_creates_draft_pr": True,
                },
                "protected_paths": ["artifacts/**", ".env", ".env.*", "**/migrations/**"],
                "public_output_forbidden_phrases": ["created by Codex"],
                "public_output_filter_applies_to": ["branch name"],
            }
        },
    }


def test_valid_low_risk_invariants_pass() -> None:
    assert validator.validate_risk_invariants(risk_payload("low")) == []


def test_valid_medium_risk_invariants_pass() -> None:
    assert validator.validate_risk_invariants(risk_payload("medium")) == []


def test_valid_high_risk_invariants_pass() -> None:
    assert validator.validate_risk_invariants(risk_payload("high")) == []


def test_policy_requires_requested_branch_prefixes() -> None:
    assert validator.validate_policy_data(policy_payload()) == []


def test_policy_rejects_old_codex_branch_prefix() -> None:
    legacy_prefix = "co" + "dex/"
    errors = validator.validate_policy_data(policy_payload(["feat/", "fix/", "issue/", legacy_prefix]))
    assert any("allowed_branch_prefixes" in error for error in errors)


def test_repository_registry_requires_trusted_branch_prefixes() -> None:
    registry = {
        "version": 1,
        "repositories": {
            "nextjs_web": {
                "project_profile": "nextjs_web",
                "expected_remotes": ["git@example.com:org/nextjs_web.git"],
                "base_branch": "main",
                "allowed_branch_prefixes": ["feat/", "fix/", "issue/", "tast/"],
                "protected_paths": [".env"],
            }
        },
    }
    assert validator.validate_registry_data(registry) == []
    registry["repositories"]["nextjs_web"]["allowed_branch_prefixes"] = ["feat/", "fix/", "issue/", "legacy/"]
    assert any("allowed_branch_prefixes" in error for error in validator.validate_registry_data(registry))


def test_empty_central_repository_registry_is_safe_by_default() -> None:
    assert validator.validate_registry_data({"version": 1, "repositories": {}}) == []


def test_repository_tool_policy_covers_role_capabilities_and_verifiers() -> None:
    policy, policy_errors = validator.load_yaml(
        validator.AGENT_TOOL_POLICY,
        ".agent-tool-policy.yaml",
    )
    capabilities, capability_errors = validator.load_yaml(
        validator.AGENT_ROLE_CAPABILITIES,
        ".agent-role-capabilities.yaml",
    )

    assert policy_errors + capability_errors == []
    assert validator.validate_tool_policy_data(policy, capabilities) == []
    assert validator.validate_verifier_contracts() == []


def test_high_with_open_pr_true_fails() -> None:
    errors = validator.validate_risk_invariants(risk_payload("high", open_pr=True))
    assert any("open_pr=false" in error for error in errors)


def test_medium_with_commit_false_fails() -> None:
    errors = validator.validate_risk_invariants(risk_payload("medium", commit=False))
    assert any("commit=true" in error for error in errors)


def test_publish_verdict_is_pre_publication_only() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(decision="publish_pr", execution_status="completed")
    )
    assert any("pre-publication decision" in error for error in errors)


def test_publish_verdict_requires_passing_checks() -> None:
    errors = validator.validate_verdict_invariants(verdict_payload(checks_passed=False))
    assert any("requires checks_passed=true" in error for error in errors)


def test_await_approval_requires_publish_approval_flag() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(decision="await_approval", approval_required_before_publish=False)
    )
    assert any("await_approval requires approval_required_before_publish=true" in error for error in errors)


def test_high_risk_publish_verdict_fails() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(
            decision="publish_pr",
            risk_class="high",
            approval_required_before_publish=True,
        )
    )
    assert any("high risk" in error for error in errors)


def test_missing_visual_evidence_publish_verdict_fails() -> None:
    errors = validator.validate_verdict_invariants(
        verdict_payload(
            visual_evidence={"required": True, "provided": False, "items": []},
        )
    )
    assert any("missing required visual evidence" in error for error in errors)


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
            "quality": {"project_profile": "nextjs_web"},
            "verdict": {"project_profile": "agent_workspace", "risk_class": "medium"},
            "risk": {"risk_class": "medium"},
        }
    )
    assert any("project profile mismatch" in error for error in errors)


def test_completed_publication_without_pr_url_fails() -> None:
    errors = validator.validate_cross_artifact_invariants(
        {
            "project_profile": {"project_profile": "agent_workspace"},
            "quality": {"project_profile": "agent_workspace"},
            "risk": {"risk_class": "medium"},
            "publication": {
                "execution_status": "completed",
                "commit_created": True,
                "branch_pushed": True,
                "pr_created_or_updated": False,
                "pr_url": "",
                "pr_state": "ready",
            },
            "verdict": {
                "project_profile": "agent_workspace",
                "risk_class": "medium",
                "execution_status": "planned",
                "decision": "publish_pr",
            },
        }
    )
    assert any("completed publication requires a PR URL" in error for error in errors)


def test_invalid_yaml_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("version: [\n", encoding="utf-8")
    _, errors = validator.load_yaml(path, "bad.yaml")
    assert errors
    assert "invalid YAML" in errors[0]


def test_json_artifacts_uses_custom_artifacts_dir(tmp_path: Path) -> None:
    mapping = validator.json_artifacts(tmp_path)

    assert mapping["risk"][0] == tmp_path / "risk.json"
    assert mapping["risk"][1].name == "risk.schema.json"
