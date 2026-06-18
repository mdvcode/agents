from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
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
    def __init__(
        self,
        responses: dict[
            tuple[str, ...], publish_pr.CommandResult | list[publish_pr.CommandResult]
        ]
        | None = None,
    ) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: Sequence[str], cwd: Path) -> publish_pr.CommandResult:
        command = tuple(args)
        self.calls.append(command)
        response = self.responses.get(command, publish_pr.CommandResult(0, "", ""))
        if isinstance(response, list):
            if not response:
                return publish_pr.CommandResult(0, "", "")
            return response.pop(0)
        return response


def quality_payload(overall_status: str = "pass") -> dict[str, object]:
    return {
        "overall_status": overall_status,
        "focused_tests_passed": overall_status == "pass",
        "repository_checks_passed": overall_status == "pass",
    }


def verdict_payload(checks_passed: bool = True, evidence_required: bool = False) -> dict[str, object]:
    return {
        "decision": "publish_pr",
        "execution_status": "planned",
        "approval_required_before_publish": False,
        "blockers": [],
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
            ("git", "rev-parse", "--is-inside-work-tree"): publish_pr.CommandResult(0, "true\n", ""),
            ("git", "diff", "--name-only", "--diff-filter=U"): publish_pr.CommandResult(0, "", ""),
            ("git", "status", "--porcelain", "--", "app/page.tsx"): publish_pr.CommandResult(
                0, " M app/page.tsx\n", ""
            ),
            ("git", "diff", "--cached", "--name-only"): publish_pr.CommandResult(0, "", ""),
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


def test_command_runner_missing_command_returns_result(tmp_path: Path) -> None:
    result = publish_pr.CommandRunner().run(["definitely-not-a-real-command-xyz"], cwd=tmp_path)
    assert result.returncode == 127
    assert result.stderr


def test_high_risk_blocks_preflight(tmp_path: Path) -> None:
    publisher = publish_pr.Publisher(runner=base_preflight_runner())
    result = publisher.preflight(
        tmp_path,
        base_policy(),
        profiles_payload(),
        {
            "risk_class": "high",
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "autonomy_allowed": {"commit": False, "push": False, "open_pr": False, "update_pr": False},
        },
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
        {
            "risk_class": "medium",
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "autonomy_allowed": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        },
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
        {
            "risk_class": "medium",
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "autonomy_allowed": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        },
        quality_payload("pass"),
        {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()},
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["app/page.tsx"]},
        skip_checks=True,
    )
    assert "missing git identity blocks publication" in result.errors


def test_verdict_await_approval_blocks_preflight(tmp_path: Path) -> None:
    publisher = publish_pr.Publisher(runner=base_preflight_runner())
    verdict = {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()}
    verdict["decision"] = "await_approval"
    result = publisher.preflight(
        tmp_path,
        base_policy(),
        profiles_payload(),
        {
            "risk_class": "medium",
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "autonomy_allowed": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        },
        quality_payload("pass"),
        verdict,
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["app/page.tsx"]},
        skip_checks=True,
    )
    assert "verdict decision must be publish_pr" in result.errors


def test_expected_remote_mismatch_blocks_preflight(tmp_path: Path) -> None:
    runner = base_preflight_runner()
    runner.responses[("git", "remote", "get-url", "origin")] = publish_pr.CommandResult(
        0, "git@example.com:other/repo.git\n", ""
    )
    publisher = publish_pr.Publisher(runner=runner)
    result = publisher.preflight(
        tmp_path,
        base_policy(),
        profiles_payload(),
        {
            "risk_class": "medium",
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "autonomy_allowed": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        },
        quality_payload("pass"),
        {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()},
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["app/page.tsx"]},
        skip_checks=True,
        expected_remote="git@example.com:expected/repo.git",
    )
    assert "target repository remote does not match expected_remote" in result.errors


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
        {
            "risk_class": "medium",
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "autonomy_allowed": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        },
        quality_payload("pass"),
        {"high_risk_triggers": [], "protected_paths_touched": [], **verdict_payload()},
        {"project_profile": "flowfox"},
        {"project_profile": "flowfox", "include": ["app/page.tsx"]},
        skip_checks=False,
    )

    assert result.execution_status == "running"
    assert result.pr_state == "draft"
    assert any("profile quality_commands command failed: bun test" in warning for warning in result.warnings)


def test_stage_change_set_dry_run_has_no_git_add(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "page.tsx").write_text("x", encoding="utf-8")
    runner = FakeRunner(
        {
            ("git", "status", "--porcelain", "--", "app/page.tsx"): publish_pr.CommandResult(
                0, " M app/page.tsx\n", ""
            ),
            ("git", "diff", "--cached", "--name-only"): publish_pr.CommandResult(0, "", ""),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)
    publication = publish_pr.PublicationResult()
    staged = publisher.stage_change_set(
        tmp_path,
        {"include": ["app/page.tsx", ".env"], "exclude": [".env"]},
        dry_run=True,
        publication=publication,
    )
    assert staged == {"app/page.tsx"}
    assert ("git", "add", "--", "app/page.tsx") not in runner.calls


def test_stage_change_set_blocks_unsafe_path(tmp_path: Path) -> None:
    publication = publish_pr.PublicationResult()
    publisher = publish_pr.Publisher(runner=FakeRunner())
    staged = publisher.stage_change_set(
        tmp_path,
        {"include": ["../outside.txt"], "exclude": []},
        dry_run=True,
        publication=publication,
    )
    assert staged == set()
    assert any("invalid change-set path" in error for error in publication.errors)


def test_git_add_failure_blocks_staging(tmp_path: Path) -> None:
    (tmp_path / "allowed.txt").write_text("changed", encoding="utf-8")
    runner = FakeRunner(
        {
            ("git", "status", "--porcelain", "--", "allowed.txt"): publish_pr.CommandResult(
                0, " M allowed.txt\n", ""
            ),
            ("git", "diff", "--cached", "--name-only"): publish_pr.CommandResult(0, "", ""),
            ("git", "add", "--", "allowed.txt"): publish_pr.CommandResult(1, "", "permission denied"),
        }
    )
    publication = publish_pr.PublicationResult()
    publisher = publish_pr.Publisher(runner=runner)
    staged = publisher.stage_change_set(
        tmp_path,
        {"include": ["allowed.txt"], "exclude": []},
        dry_run=False,
        publication=publication,
    )
    assert staged == set()
    assert any("git add failed for allowed.txt" in error for error in publication.errors)


def test_existing_pr_updates_without_duplicate_create(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("gh", "pr", "view", "--json", "number,url"): publish_pr.CommandResult(
                0, json.dumps({"number": 123, "url": "https://github.com/org/repo/pull/123"}), ""
            ),
            ("gh", "pr", "view", "--json", "number,url,isDraft"): [
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {"number": 123, "url": "https://github.com/org/repo/pull/123", "isDraft": True}
                    ),
                    "",
                ),
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {"number": 123, "url": "https://github.com/org/repo/pull/123", "isDraft": False}
                    ),
                    "",
                ),
            ],
            ("gh", "pr", "edit", "--title", "Title", "--body", "Body"): publish_pr.CommandResult(0, "", ""),
            ("gh", "pr", "ready"): publish_pr.CommandResult(0, "", ""),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)
    created, number, url, state, error = publisher.create_or_update_pr(tmp_path, "Title", "Body", "ready")
    assert created is True
    assert number == 123
    assert url.endswith("/123")
    assert state == "ready"
    assert error == ""
    assert ("gh", "pr", "create", "--title", "Title", "--body", "Body") not in runner.calls


def test_existing_ready_pr_moves_back_to_draft(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("gh", "pr", "view", "--json", "number,url,isDraft"): [
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {"number": 123, "url": "https://github.com/org/repo/pull/123", "isDraft": False}
                    ),
                    "",
                ),
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {"number": 123, "url": "https://github.com/org/repo/pull/123", "isDraft": True}
                    ),
                    "",
                ),
            ],
            ("gh", "pr", "edit", "--title", "Title", "--body", "Body"): publish_pr.CommandResult(0, "", ""),
            ("gh", "pr", "ready", "--undo"): publish_pr.CommandResult(0, "", ""),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)
    created, number, url, state, error = publisher.create_or_update_pr(tmp_path, "Title", "Body", "draft")
    assert created is True
    assert number == 123
    assert url.endswith("/123")
    assert state == "draft"
    assert error == ""


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
            ("gh", "pr", "view", "--json", "number,url,isDraft"): publish_pr.CommandResult(
                1, "", "not found"
            ),
            ("gh", "pr", "create", "--title", "Title", "--body", "Body", "--draft"): publish_pr.CommandResult(
                1, "", "api unavailable"
            ),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)
    created, number, url, state, error = publisher.create_or_update_pr(tmp_path, "Title", "Body", "draft")
    assert created is False
    assert number == 0
    assert url == ""
    assert state == "not_created"
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


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


def prepare_publish_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    repo = root / "repo"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    repo.mkdir(parents=True)
    (repo / "app").mkdir()
    (repo / "app" / "page.tsx").write_text("changed", encoding="utf-8")
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
    security_commands:
      required: []
""".lstrip(),
        encoding="utf-8",
    )
    write_json(
        artifacts / "risk.json",
        {
            "risk_class": "medium",
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "autonomy_allowed": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        },
    )
    write_json(
        artifacts / "quality.json",
        {"overall_status": "pass", "focused_tests_passed": True, "repository_checks_passed": True},
    )
    write_json(
        artifacts / "verdict.json",
        {
            "decision": "publish_pr",
            "execution_status": "planned",
            "approval_required_before_publish": False,
            "blockers": [],
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
            "target_repository": "repo",
            "project_profile": "agent_workspace",
            "task_id": "issue-943-test",
            "expected_remote": "",
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
            "run_id": "",
            "run_dir": "",
            "pr_comment_posted": False,
            "command_results": [],
            "warnings": [],
            "errors": [],
        },
    )
    (artifacts / "report.md").write_text("# Report\n", encoding="utf-8")
    (artifacts / "audit_log.jsonl").write_text("", encoding="utf-8")
    return root, repo


def test_publish_dry_run_does_not_mutate_artifacts(tmp_path: Path, monkeypatch: object) -> None:
    root, _ = prepare_publish_root(tmp_path)
    monkeypatch.setenv("AGENT_HARNESS_TEST_MODE", "1")
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


def test_skip_checks_is_blocked_outside_test_dry_run(tmp_path: Path) -> None:
    root, _ = prepare_publish_root(tmp_path)
    publisher = publish_pr.Publisher(root=root, runner=base_preflight_runner())
    result = publisher.publish(dry_run=False, skip_checks=True)
    assert result.execution_status == "blocked"
    assert any("--skip-checks is only allowed" in error for error in result.errors)


def test_missing_target_repository_blocks_without_traceback(tmp_path: Path) -> None:
    root, _ = prepare_publish_root(tmp_path)
    write_json(
        root / "artifacts" / "change_set.json",
        {
            "target_repository": "missing",
            "project_profile": "agent_workspace",
            "task_id": "issue-943-test",
            "expected_remote": "",
            "include": ["app/page.tsx"],
            "exclude": [],
        },
    )
    publisher = publish_pr.Publisher(root=root, runner=base_preflight_runner())
    result = publisher.publish(dry_run=True)
    assert result.execution_status == "blocked"
    assert "target repository does not exist" in result.errors


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


def test_record_publication_replaces_existing_marked_sections(tmp_path: Path) -> None:
    root, _ = prepare_publish_root(tmp_path)
    publisher = publish_pr.Publisher(root=root, runner=base_preflight_runner())
    first = publish_pr.PublicationResult(
        execution_status="completed",
        branch="issue-943",
        commit_sha="old",
        pr_url="https://github.com/org/repo/pull/1",
        pr_state="ready",
    )
    second = publish_pr.PublicationResult(
        execution_status="completed",
        branch="issue-943",
        commit_sha="new",
        pr_url="https://github.com/org/repo/pull/2",
        pr_state="ready",
    )
    publisher.record_publication(first, {"task_id": "task"}, "agent_workspace")
    publisher.record_publication(second, {"task_id": "task"}, "agent_workspace")
    report_text = (root / "artifacts" / "report.md").read_text(encoding="utf-8")
    assert report_text.count(publish_pr.PUBLICATION_RESULT_START) == 1
    assert "pull/1" not in report_text
    assert "pull/2" in report_text


def test_pre_staged_unrelated_file_blocks_publication(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init")
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "config", "user.email", "test@example.com")
    (repo / "allowed.txt").write_text("old\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("old\n", encoding="utf-8")
    run_git(repo, "add", "allowed.txt", "unrelated.txt")
    run_git(repo, "commit", "-m", "initial")
    (repo / "allowed.txt").write_text("new\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("new\n", encoding="utf-8")
    run_git(repo, "add", "unrelated.txt")
    publisher = publish_pr.Publisher(runner=publish_pr.CommandRunner())
    publication = publish_pr.PublicationResult()

    staged = publisher.stage_change_set(
        repo,
        {"include": ["allowed.txt"], "exclude": []},
        dry_run=False,
        publication=publication,
    )

    assert staged == set()
    assert any("pre-existing staged files outside change set" in error for error in publication.errors)


def prepare_e2e_root(tmp_path: Path, repo: Path, remote_url: str) -> Path:
    root = tmp_path / "root"
    artifacts = root / "artifacts"
    scripts = root / "scripts"
    artifacts.mkdir(parents=True)
    scripts.mkdir()
    (scripts / "validate_artifacts.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (scripts / "security_scan.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (root / ".agent-policy.yaml").write_text(
        """
version: 1
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
version: 1
profiles:
  agent_workspace:
    quality_commands:
      required:
        - "git status --short"
    security_commands:
      required:
        - "git diff --check"
""".lstrip(),
        encoding="utf-8",
    )
    write_json(
        artifacts / "risk.json",
        {
            "risk_class": "medium",
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "autonomy_allowed": {"commit": True, "push": True, "open_pr": True, "update_pr": True},
        },
    )
    write_json(
        artifacts / "quality.json",
        {"overall_status": "pass", "focused_tests_passed": True, "repository_checks_passed": True},
    )
    write_json(
        artifacts / "verdict.json",
        {
            "decision": "publish_pr",
            "execution_status": "planned",
            "approval_required_before_publish": False,
            "blockers": [],
            "checks_passed": True,
            "high_risk_triggers": [],
            "protected_paths_touched": [],
            "warnings": [],
            "flowfox_visual_evidence": {"required": False, "provided": False, "items": []},
            "publication_result": {
                "commit_created": False,
                "branch_pushed": False,
                "pr_created_or_updated": False,
                "pr_url": "",
                "pr_state": "not_created",
            },
        },
    )
    write_json(artifacts / "project_profile.json", {"project_profile": "agent_workspace"})
    write_json(
        artifacts / "change_set.json",
        {
            "target_repository": ".",
            "project_profile": "agent_workspace",
            "task_id": "issue-943-e2e",
            "expected_remote": remote_url,
            "include": ["allowed.txt"],
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
            "run_id": "",
            "run_dir": "",
            "pr_comment_posted": False,
            "command_results": [],
            "warnings": [],
            "errors": [],
        },
    )
    write_json(
        artifacts / "publication_payload.json",
        {
            "title": "Update allowed file",
            "body": "Safe public summary.",
            "commit_message": "Update allowed file",
            "base_branch": "main",
        },
    )
    (artifacts / "report.md").write_text("# Report\n", encoding="utf-8")
    (artifacts / "audit_log.jsonl").write_text("", encoding="utf-8")
    return root


def write_fake_gh(bin_dir: Path, state_file: Path) -> None:
    gh = bin_dir / "gh"
    gh.write_text(
        f"""#!/bin/sh
set -eu
STATE="{state_file}"
if [ "$1" = "auth" ]; then
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  if [ -f "$STATE" ]; then
    cat "$STATE"
    exit 0
  fi
  exit 1
fi
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  echo '{{"number":123,"url":"https://github.com/org/repo/pull/123","isDraft":false}}' > "$STATE"
  echo "https://github.com/org/repo/pull/123"
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "edit" ]; then
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "ready" ]; then
  echo '{{"number":123,"url":"https://github.com/org/repo/pull/123","isDraft":false}}' > "$STATE"
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "comment" ]; then
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)


def test_end_to_end_publication_with_temp_git_repo_and_fake_gh(
    tmp_path: Path, monkeypatch: object
) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    repo.mkdir()
    run_git(repo, "init")
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "remote", "add", "origin", str(remote))
    (repo / "allowed.txt").write_text("old\n", encoding="utf-8")
    run_git(repo, "add", "allowed.txt")
    run_git(repo, "commit", "-m", "initial")
    run_git(repo, "checkout", "-b", "issue-943")
    (repo / "allowed.txt").write_text("new\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_gh(bin_dir, tmp_path / "gh-state.json")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    tracked_before = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            root / "artifacts" / "publication.json",
            root / "artifacts" / "verdict.json",
            root / "artifacts" / "report.md",
            root / "artifacts" / "audit_log.jsonl",
        )
    }
    publisher = publish_pr.Publisher(root=root)

    publication = publisher.publish(repo_override=repo)

    assert publication.execution_status == "completed"
    assert publication.commit_created is True
    assert publication.branch_pushed is True
    assert publication.pr_created_or_updated is True
    assert publication.pr_url == "https://github.com/org/repo/pull/123"
    assert publication.pr_comment_posted is True
    assert publication.run_dir
    assert (Path(publication.run_dir) / "publication.json").exists()
    assert (Path(publication.run_dir) / "publication.md").exists()
    tracked_after = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            root / "artifacts" / "publication.json",
            root / "artifacts" / "verdict.json",
            root / "artifacts" / "report.md",
            root / "artifacts" / "audit_log.jsonl",
        )
    }
    assert tracked_before == tracked_after
    committed_files = run_git(repo, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert committed_files == ["allowed.txt"]
