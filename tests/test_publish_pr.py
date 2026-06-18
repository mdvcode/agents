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


def profiles_payload(command: str = "make check") -> dict[str, object]:
    return {
        "profiles": {
            "flowfox": {
                "quality_commands": {
                    "required": [command],
                }
            },
            "agent_workspace": {
                "quality_commands": {
                    "required": [command],
                }
            },
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
        profiles_payload(),
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
        profiles_payload(),
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
        profiles_payload(),
        {"risk_class": "medium", "high_risk_triggers": [], "protected_paths_touched": []},
        quality_payload("pass"),
        {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()},
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["app/page.tsx"]},
        skip_checks=True,
    )
    assert "missing git identity blocks publication" in result.errors


def test_profile_quality_command_failure_drafts_pr(tmp_path: Path) -> None:
    runner = base_preflight_runner()
    runner.responses[("python3", "scripts/validate_artifacts.py")] = publish_pr.CommandResult(0, "", "")
    runner.responses[
        ("python3", "scripts/security_scan.py", "--repo", str(tmp_path), "--profile", "flowfox")
    ] = publish_pr.CommandResult(0, "", "")
    runner.responses[("bun", "test")] = publish_pr.CommandResult(1, "", "tests failed")
    publisher = publish_pr.Publisher(runner=runner)

    result = publisher.preflight(
        tmp_path,
        base_policy(),
        profiles_payload("bun test"),
        {"risk_class": "medium", "high_risk_triggers": [], "protected_paths_touched": []},
        quality_payload("pass"),
        {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()},
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["app/page.tsx"]},
        skip_checks=False,
    )

    assert result.execution_status == "running"
    assert result.pr_state == "draft"
    assert any("profile quality command failed: bun test" in warning for warning in result.warnings)


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


def write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def prepare_publish_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    repo = tmp_path / "repo"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    repo.mkdir()
    (root / ".agent-policy.yaml").write_text(
        """
projects:
  flowfox:
    protected_paths:
      - "artifacts/**"
    public_output_forbidden_phrases:
      - "created by Codex"
""".lstrip(),
        encoding="utf-8",
    )
    (root / ".agent-project-profiles.yaml").write_text(
        """
profiles:
  agent_workspace:
    quality_commands:
      required:
        - "make check"
""".lstrip(),
        encoding="utf-8",
    )
    write_json(
        artifacts / "risk.json",
        {"risk_class": "medium", "high_risk_triggers": [], "protected_paths_touched": []},
    )
    write_json(
        artifacts / "quality.json",
        {"overall_status": "pass", "focused_tests_passed": True, "repository_checks_passed": True},
    )
    write_json(
        artifacts / "verdict.json",
        {
            "checks_passed": True,
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "warnings": [],
            "flowfox_visual_evidence": {"required": False, "provided": False, "items": []},
        },
    )
    write_json(artifacts / "project_profile.json", {"project_profile": "agent_workspace"})
    write_json(
        artifacts / "change_set.json",
        {
            "target_repository": str(repo),
            "project_profile": "agent_workspace",
            "task_id": "issue-943-test",
            "include": ["app/page.tsx"],
            "exclude": [],
        },
    )
    write_json(
        artifacts / "publication.json",
        {
            "execution_status": "planned",
            "branch": "",
            "commit_created": False,
            "commit_sha": "",
            "branch_pushed": False,
            "pr_created_or_updated": False,
            "pr_number": 0,
            "pr_url": "",
            "pr_state": "not_created",
            "dry_run": False,
            "warnings": [],
            "errors": [],
        },
    )
    (artifacts / "report.md").write_text("# Report\n", encoding="utf-8")
    (artifacts / "audit_log.jsonl").write_text("", encoding="utf-8")
    return root, repo


def test_publish_dry_run_does_not_mutate_artifacts(tmp_path: Path) -> None:
    root, _ = prepare_publish_root(tmp_path)
    artifacts = root / "artifacts"
    before = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            artifacts / "publication.json",
            artifacts / "verdict.json",
            artifacts / "report.md",
            artifacts / "audit_log.jsonl",
        )
    }
    publisher = publish_pr.Publisher(root=root, runner=base_preflight_runner())

    result = publisher.publish(dry_run=True, skip_checks=True)

    after = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            artifacts / "publication.json",
            artifacts / "verdict.json",
            artifacts / "report.md",
            artifacts / "audit_log.jsonl",
        )
    }
    assert result.execution_status == "planned"
    assert before == after


def test_record_publication_updates_report_and_issue_journal(tmp_path: Path) -> None:
    root, _ = prepare_publish_root(tmp_path)
    issue_path = root / "docs" / "projects" / "flowfox" / "issues" / "issue-943.md"
    issue_path.parent.mkdir(parents=True)
    issue_path.write_text("# Issue 943\n", encoding="utf-8")
    publisher = publish_pr.Publisher(root=root, runner=base_preflight_runner())
    publication = publish_pr.PublicationResult(
        execution_status="completed",
        branch="issue-943",
        commit_created=True,
        commit_sha="abc123",
        branch_pushed=True,
        pr_created_or_updated=True,
        pr_number=123,
        pr_url="https://github.com/org/repo/pull/123",
        pr_state="ready",
    )

    publisher.record_publication(
        publication,
        {"task_id": "issue-943-test"},
        "flowfox",
    )

    report_text = (root / "artifacts" / "report.md").read_text(encoding="utf-8")
    issue_text = issue_path.read_text(encoding="utf-8")
    assert "## Publication Result" in report_text
    assert "https://github.com/org/repo/pull/123" in report_text
    assert "## Publication Result" in issue_text
    assert "https://github.com/org/repo/pull/123" in issue_text
