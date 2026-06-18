from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Sequence


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "publish_pr.py"
SPEC = importlib.util.spec_from_file_location("publish_pr", MODULE_PATH)
assert SPEC is not None
publish_pr = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = publish_pr
SPEC.loader.exec_module(publish_pr)


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], publish_pr.CommandResult] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: Sequence[str], cwd: Path) -> publish_pr.CommandResult:
        command = tuple(args)
        self.calls.append(command)
        return self.responses.get(command, publish_pr.CommandResult(0, "", ""))


def quality_payload(overall_status: str = "pass") -> dict[str, object]:
    return {
        "overall_status": overall_status,
        "focused_tests_passed": overall_status == "pass",
        "repository_checks_passed": overall_status == "pass",
    }


def verdict_payload(checks_passed: bool = True, evidence_required: bool = False) -> dict[str, object]:
    return {
        "checks_passed": checks_passed,
        "flowfox_visual_evidence": {
            "required": evidence_required,
            "provided": not evidence_required,
            "items": [],
        },
    }


def base_policy() -> dict[str, object]:
    return {
        "projects": {
            "flowfox": {
                "protected_paths": ["artifacts/**", "docs/projects/**", ".env", ".env.*"],
                "public_output_forbidden_phrases": ["created by Codex"],
            }
        }
    }


def base_preflight_runner() -> FakeRunner:
    return FakeRunner(
        {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): publish_pr.CommandResult(0, "issue-943\n", ""),
            ("git", "diff", "--name-only", "--diff-filter=U"): publish_pr.CommandResult(0, "", ""),
            ("git", "config", "user.name"): publish_pr.CommandResult(0, "Daryna\n", ""),
            ("git", "config", "user.email"): publish_pr.CommandResult(0, "daryna@example.com\n", ""),
            ("git", "remote"): publish_pr.CommandResult(0, "origin\n", ""),
            ("gh", "auth", "status"): publish_pr.CommandResult(0, "ok\n", ""),
        }
    )


def test_low_checks_pass_ready_pr_state() -> None:
    state = publish_pr.determine_pr_state(quality_payload("pass"), verdict_payload())
    assert state == "ready"


def test_medium_failed_checks_draft_pr_state() -> None:
    state = publish_pr.determine_pr_state(quality_payload("fail"), verdict_payload(checks_passed=False))
    assert state == "draft"


def test_missing_visual_evidence_draft_pr_state() -> None:
    state = publish_pr.determine_pr_state(quality_payload("pass"), verdict_payload(evidence_required=True))
    assert state == "draft"


def test_high_risk_blocks_preflight(tmp_path: Path) -> None:
    publisher = publish_pr.Publisher(runner=base_preflight_runner())
    result = publisher.preflight(
        tmp_path,
        base_policy(),
        {"risk_class": "high", "high_risk_triggers": [], "protected_paths_touched": []},
        quality_payload("pass"),
        {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()},
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["app/page.tsx"]},
        skip_checks=True,
    )
    assert result.execution_status == "blocked"
    assert any("HIGH risk" in error for error in result.errors)


def test_protected_path_blocks_preflight(tmp_path: Path) -> None:
    publisher = publish_pr.Publisher(runner=base_preflight_runner())
    result = publisher.preflight(
        tmp_path,
        base_policy(),
        {"risk_class": "medium", "high_risk_triggers": [], "protected_paths_touched": []},
        quality_payload("pass"),
        {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()},
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["artifacts/risk.json"]},
        skip_checks=True,
    )
    assert any("protected path in change set: artifacts/risk.json" in error for error in result.errors)


def test_missing_git_identity_blocks_preflight(tmp_path: Path) -> None:
    runner = base_preflight_runner()
    runner.responses[("git", "config", "user.email")] = publish_pr.CommandResult(1, "", "")
    publisher = publish_pr.Publisher(runner=runner)
    result = publisher.preflight(
        tmp_path,
        base_policy(),
        {"risk_class": "medium", "high_risk_triggers": [], "protected_paths_touched": []},
        quality_payload("pass"),
        {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()},
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["app/page.tsx"]},
        skip_checks=True,
    )
    assert "missing git identity blocks publication" in result.errors


def test_stage_change_set_dry_run_has_no_git_add(tmp_path: Path) -> None:
    runner = FakeRunner()
    publisher = publish_pr.Publisher(runner=runner)
    staged = publisher.stage_change_set(
        tmp_path,
        {"include": ["app/page.tsx", ".env"], "exclude": [".env"]},
        dry_run=True,
    )
    assert staged == ["app/page.tsx"]
    assert not runner.calls


def test_existing_pr_updates_without_duplicate_create(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("gh", "pr", "view", "--json", "number,url"): publish_pr.CommandResult(
                0, json.dumps({"number": 123, "url": "https://github.com/org/repo/pull/123"}), ""
            ),
            ("gh", "pr", "edit", "--title", "Title", "--body", "Body"): publish_pr.CommandResult(0, "", ""),
            ("gh", "pr", "ready"): publish_pr.CommandResult(0, "", ""),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)
    created, number, url, error = publisher.create_or_update_pr(tmp_path, "Title", "Body", "ready")
    assert created is True
    assert number == 123
    assert url.endswith("/123")
    assert error == ""
    assert ("gh", "pr", "create", "--title", "Title", "--body", "Body") not in runner.calls


def test_push_failure_returns_error(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("git", "push", "--set-upstream", "origin", "issue-943"): publish_pr.CommandResult(
                1, "", "network down"
            )
        }
    )
    publisher = publish_pr.Publisher(runner=runner)
    assert publisher.push(tmp_path, "issue-943") == "network down"


def test_pr_creation_failure_records_error(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("gh", "pr", "view", "--json", "number,url"): publish_pr.CommandResult(1, "", "not found"),
            ("gh", "pr", "create", "--title", "Title", "--body", "Body", "--draft"): publish_pr.CommandResult(
                1, "", "api unavailable"
            ),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)
    created, number, url, error = publisher.create_or_update_pr(tmp_path, "Title", "Body", "draft")
    assert created is False
    assert number == 0
    assert url == ""
    assert error == "api unavailable"


def test_forbidden_public_output_blocks_internal_process_phrase() -> None:
    errors = publish_pr.forbidden_public_output_blockers(
        base_policy(),
        "issue-943",
        "Title",
        "created by Codex",
    )
    assert errors == ["public output contains forbidden phrase: created by Codex"]
