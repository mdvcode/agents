from __future__ import annotations

import importlib.util
import json
import os
import shutil
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
        "visual_evidence": {
            "required": evidence_required,
            "provided": not evidence_required,
            "items": [],
        },
    }


def base_policy() -> dict[str, object]:
    return {
        "projects": {
            "nextjs_web": {
                "publication": {
                    "allowed_branch_prefixes": ["feat/", "fix/", "issue/", "tast/"],
                },
                "protected_paths": ["artifacts/**", "docs/projects/**", ".env", ".env.*"],
                "public_output_forbidden_phrases": ["created by Codex"],
            }
        }
    }


def profiles_payload(command: str = "make check") -> dict[str, object]:
    return {
        "profiles": {
            "nextjs_web": {
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
        {"project_profile": "nextjs_web"},
        {"project_profile": "nextjs_web", "include": ["app/page.tsx"]},
        skip_checks=True,
    )
    assert result.execution_status == "blocked"
    assert "Publication is not permitted by the current verdict or policy." in result.errors


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
        {"project_profile": "nextjs_web"},
        {"project_profile": "nextjs_web", "include": ["artifacts/risk.json"]},
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
        {"project_profile": "nextjs_web"},
        {"project_profile": "nextjs_web", "include": ["app/page.tsx"]},
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
        {"project_profile": "nextjs_web"},
        {"project_profile": "nextjs_web", "include": ["app/page.tsx"]},
        skip_checks=True,
    )
    assert "Publication is not permitted by the current verdict or policy." in result.errors


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
        {"project_profile": "nextjs_web"},
        {"project_profile": "nextjs_web", "include": ["app/page.tsx"]},
        skip_checks=True,
        expected_remote="git@example.com:expected/repo.git",
    )
    assert "target repository remote does not match expected_remote" in result.errors


def test_profile_quality_command_failure_drafts_pr(tmp_path: Path) -> None:
    runner = base_preflight_runner()
    runner.responses[("python3", "scripts/validate_artifacts.py")] = publish_pr.CommandResult(0, "", "")
    runner.responses[
        ("python3", "scripts/security_scan.py", "--repo", str(tmp_path), "--profile", "nextjs_web")
    ] = publish_pr.CommandResult(0, "", "")
    runner.responses[("bun", "test")] = publish_pr.CommandResult(1, "", "tests failed")
    publisher = publish_pr.Publisher(runner=runner)

    publication = publish_pr.PublicationResult(pr_state="ready")
    publisher.run_profile_commands(
        tmp_path,
        profiles_payload("bun test"),
        "nextjs_web",
        publication,
        "quality_commands",
    )

    assert publication.pr_state == "draft"
    assert any("profile quality_commands command failed: bun test" in warning for warning in publication.warnings)


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
            ("gh", "pr", "view", "--json", "number,url,isDraft,baseRefName,headRefName"): [
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {
                            "number": 123,
                            "url": "https://github.com/org/repo/pull/123",
                            "isDraft": True,
                            "baseRefName": "main",
                            "headRefName": "issue-943",
                        }
                    ),
                    "",
                ),
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {
                            "number": 123,
                            "url": "https://github.com/org/repo/pull/123",
                            "isDraft": False,
                            "baseRefName": "main",
                            "headRefName": "issue-943",
                        }
                    ),
                    "",
                ),
            ],
            ("gh", "pr", "edit", "123", "--title", "Title", "--body", "Body"): publish_pr.CommandResult(0, "", ""),
            ("gh", "pr", "ready", "123"): publish_pr.CommandResult(0, "", ""),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)
    created, number, url, state, error = publisher.create_or_update_pr(tmp_path, "Title", "Body", "ready")
    assert created is True
    assert number == 123
    assert url.endswith("/123")
    assert state == "ready"
    assert error == ""
    assert not any(call[:3] == ("gh", "pr", "create") for call in runner.calls)


def test_existing_ready_pr_moves_back_to_draft(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("gh", "pr", "view", "--json", "number,url,isDraft,baseRefName,headRefName"): [
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {
                            "number": 123,
                            "url": "https://github.com/org/repo/pull/123",
                            "isDraft": False,
                            "baseRefName": "main",
                            "headRefName": "issue-943",
                        }
                    ),
                    "",
                ),
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {
                            "number": 123,
                            "url": "https://github.com/org/repo/pull/123",
                            "isDraft": True,
                            "baseRefName": "main",
                            "headRefName": "issue-943",
                        }
                    ),
                    "",
                ),
            ],
            ("gh", "pr", "edit", "123", "--title", "Title", "--body", "Body"): publish_pr.CommandResult(0, "", ""),
            ("gh", "pr", "ready", "123", "--undo"): publish_pr.CommandResult(0, "", ""),
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
            ("gh", "pr", "view", "--json", "number,url,isDraft,baseRefName,headRefName"): publish_pr.CommandResult(
                1, "", "not found"
            ),
            (
                "gh",
                "pr",
                "create",
                "--base",
                "main",
                "--title",
                "Title",
                "--body",
                "Body",
                "--draft",
            ): publish_pr.CommandResult(1, "", "api unavailable"),
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
        "nextjs_web",
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
  nextjs_web:
    publication:
      allowed_branch_prefixes:
        - "feat/"
        - "fix/"
        - "issue/"
        - "tast/"
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
            "visual_evidence": {"required": False, "provided": False, "items": []},
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
    assert "Target repository does not exist." in result.errors


def test_record_publication_updates_report_and_issue_journal(tmp_path: Path) -> None:
    root, _ = prepare_publish_root(tmp_path)
    issue_path = root / "docs" / "projects" / "example_webapp" / "issues" / "issue-943.md"
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
        {"task_id": "issue-943-test", "project": "example_webapp"},
        "nextjs_web",
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
  nextjs_web:
    publication:
      allowed_branch_prefixes:
        - "feat/"
        - "fix/"
        - "issue/"
        - "tast/"
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
            "visual_evidence": {"required": False, "provided": False, "items": []},
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
            "exclude": ["unrelated.txt"],
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
LOG="$STATE.log"
echo "$@" >> "$LOG"
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
  DRAFT=false
  BASE=main
  for arg in "$@"; do
    if [ "$arg" = "--draft" ]; then
      DRAFT=true
    fi
  done
  previous=""
  for arg in "$@"; do
    if [ "$previous" = "--base" ]; then
      BASE="$arg"
    fi
    previous="$arg"
  done
  echo "{{\\"number\\":123,\\"url\\":\\"https://github.com/org/repo/pull/123\\",\\"isDraft\\":$DRAFT,\\"baseRefName\\":\\"$BASE\\",\\"headRefName\\":\\"issue/943-e2e\\"}}" > "$STATE"
  echo "https://github.com/org/repo/pull/123"
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "edit" ]; then
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "ready" ]; then
  echo '{{"number":123,"url":"https://github.com/org/repo/pull/123","isDraft":false,"baseRefName":"main","headRefName":"issue/943-e2e"}}' > "$STATE"
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "comment" ]; then
  if [ -f "$STATE.comment-fail" ]; then
    echo "comment failed" >&2
    exit 1
  fi
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
    run_git(repo, "branch", "-M", "main")
    run_git(repo, "push", "-u", "origin", "main")
    run_git(repo, "checkout", "-b", "issue-943")
    (repo / "allowed.txt").write_text("new\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("do not publish\n", encoding="utf-8")
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
    assert tracked_before != tracked_after
    final_publication = json.loads((root / "artifacts" / "publication.json").read_text(encoding="utf-8"))
    final_verdict = json.loads((root / "artifacts" / "verdict.json").read_text(encoding="utf-8"))
    runtime_summary = json.loads((Path(publication.run_dir) / "summary.json").read_text(encoding="utf-8"))
    assert final_publication["execution_status"] == "completed"
    assert final_publication["commit_sha"] == publication.commit_sha
    assert final_publication["pr_url"] == publication.pr_url
    assert final_verdict["execution_status"] == final_publication["execution_status"]
    assert runtime_summary["execution_status"] == final_publication["execution_status"]
    assert runtime_summary["warnings"] == final_publication["warnings"]
    assert runtime_summary["errors"] == final_publication["errors"]
    committed_files = run_git(repo, "show", "--name-only", "--format=", publication.commit_sha).stdout.splitlines()
    assert committed_files == ["allowed.txt"]


def init_publication_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
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
    run_git(repo, "branch", "-M", "main")
    run_git(repo, "push", "-u", "origin", "main")
    (repo / "allowed.txt").write_text("new\n", encoding="utf-8")
    return remote, repo, tmp_path


def install_fake_gh(tmp_path: Path, monkeypatch: object) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    state_file = tmp_path / "gh-state.json"
    write_fake_gh(bin_dir, state_file)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    return state_file


def test_selected_unstaged_secret_blocks_before_git_add(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    (repo / "allowed.txt").write_text("token='ghp_" + ("A" * 24) + "'\n", encoding="utf-8")
    install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts" / "security_scan.py", root / "scripts" / "security_scan.py")

    publication = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert publication.execution_status == "blocked"
    assert publication.commit_sha == ""
    assert any("required security scan failed" in error for error in publication.errors)


def test_required_security_command_failure_blocks_publication(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    (root / ".agent-project-profiles.yaml").write_text(
        """
version: 1
profiles:
  agent_workspace:
    quality_commands:
      required: []
    security_commands:
      required:
        - "false"
""".lstrip(),
        encoding="utf-8",
    )

    publication = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert publication.execution_status == "blocked"
    assert any("required security command failed: false" in error for error in publication.errors)
    assert publication.commit_sha == ""


def test_quality_command_failure_creates_draft_pr(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    (root / ".agent-project-profiles.yaml").write_text(
        """
version: 1
profiles:
  agent_workspace:
    quality_commands:
      required:
        - "false"
    security_commands:
      required: []
""".lstrip(),
        encoding="utf-8",
    )

    publication = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert publication.execution_status == "completed"
    assert publication.pr_state == "draft"
    assert publication.pr_created_or_updated is True


def test_base_branch_from_payload_is_passed_to_gh_create(tmp_path: Path) -> None:
    body_file = tmp_path / "body.md"
    body_file.write_text("Body", encoding="utf-8")
    runner = FakeRunner(
        {
            ("gh", "pr", "view", "--json", "number,url,isDraft,baseRefName,headRefName"): [
                publish_pr.CommandResult(1, "", "not found"),
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {
                            "number": 4,
                            "url": "https://github.com/org/repo/pull/4",
                            "isDraft": False,
                            "baseRefName": "develop",
                            "headRefName": "issue/4",
                        }
                    ),
                    "",
                ),
            ],
            (
                "gh",
                "pr",
                "create",
                "--base",
                "develop",
                "--title",
                "Title",
                "--head",
                "issue/4",
                "--body-file",
                str(body_file),
            ): publish_pr.CommandResult(0, "https://github.com/org/repo/pull/4\n", ""),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)

    created, _number, _url, state, error = publisher.create_or_update_pr(
        tmp_path,
        "Title",
        "Body",
        "ready",
        base_branch="develop",
        branch="issue/4",
        body_file=body_file,
    )

    assert created is True
    assert state == "ready"
    assert error == ""
    assert any(call[:6] == ("gh", "pr", "create", "--base", "develop", "--title") for call in runner.calls)


def test_existing_pr_base_branch_is_corrected(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("gh", "pr", "view", "--json", "number,url,isDraft,baseRefName,headRefName"): [
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {
                            "number": 7,
                            "url": "https://github.com/org/repo/pull/7",
                            "isDraft": False,
                            "baseRefName": "develop",
                            "headRefName": "issue/7",
                        }
                    ),
                    "",
                ),
                publish_pr.CommandResult(
                    0,
                    json.dumps(
                        {
                            "number": 7,
                            "url": "https://github.com/org/repo/pull/7",
                            "isDraft": False,
                            "baseRefName": "main",
                            "headRefName": "issue/7",
                        }
                    ),
                    "",
                ),
            ],
            (
                "gh",
                "pr",
                "edit",
                "7",
                "--title",
                "Title",
                "--body",
                "Body",
                "--base",
                "main",
            ): publish_pr.CommandResult(0, "", ""),
        }
    )
    publisher = publish_pr.Publisher(runner=runner)

    created, number, url, state, error = publisher.create_or_update_pr(
        tmp_path,
        "Title",
        "Body",
        "ready",
        base_branch="main",
    )

    assert created is True
    assert number == 7
    assert url.endswith("/7")
    assert state == "ready"
    assert error == ""


def test_push_failure_resume_reuses_existing_commit(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, "")

    class PushFailPublisher(publish_pr.Publisher):
        def push(self, target_repo: Path, branch: str) -> str:
            return "network down"

    publisher = PushFailPublisher(root=root)

    first = publisher.publish(repo_override=repo)
    assert first.execution_status == "failed"
    assert first.commit_sha
    assert first.push_completed is False

    verdict_path = root / "artifacts" / "verdict.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["execution_status"] = "planned"
    verdict["blockers"] = []
    verdict_path.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    second = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert second.execution_status == "completed"
    assert second.commit_sha == first.commit_sha
    assert second.push_completed is True


def test_push_success_pr_failure_resume_creates_pr_without_new_commit(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    state_file = install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    gh_path = tmp_path / "bin" / "gh"
    gh_path.write_text(
        gh_path.read_text(encoding="utf-8").replace(
            'if [ "$1" = "pr" ] && [ "$2" = "create" ]; then',
            'if [ "$1" = "pr" ] && [ "$2" = "create" ] && [ ! -f "' + str(state_file) + '.allow-create" ]; then\n  echo "api unavailable" >&2\n  exit 1\nfi\nif [ "$1" = "pr" ] && [ "$2" = "create" ]; then',
        ),
        encoding="utf-8",
    )
    first = publish_pr.Publisher(root=root).publish(repo_override=repo)
    assert first.execution_status == "failed"
    assert first.push_completed is True
    assert first.commit_sha

    Path(str(state_file) + ".allow-create").write_text("1", encoding="utf-8")
    verdict_path = root / "artifacts" / "verdict.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["execution_status"] = "planned"
    verdict["blockers"] = []
    verdict_path.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    second = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert second.execution_status == "completed"
    assert second.commit_sha == first.commit_sha
    assert second.pr_created_or_updated is True


def test_second_completed_run_is_noop(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    state_file = install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    first = publish_pr.Publisher(root=root).publish(repo_override=repo)
    assert first.execution_status == "completed"
    log_before = Path(str(state_file) + ".log").read_text(encoding="utf-8")

    second = publish_pr.Publisher(root=root).publish(repo_override=repo)
    log_after = Path(str(state_file) + ".log").read_text(encoding="utf-8")

    assert second.execution_status == "completed"
    assert second.commit_sha == first.commit_sha
    assert "publication already completed; no-op" in second.warnings
    assert log_after == log_before


def test_invalid_risk_json_returns_structured_blocked_result(tmp_path: Path) -> None:
    root, repo = prepare_publish_root(tmp_path)
    (root / "artifacts" / "risk.json").write_text("{invalid", encoding="utf-8")

    publication = publish_pr.Publisher(root=root, runner=base_preflight_runner()).publish(repo_override=repo)

    assert publication.execution_status == "blocked"
    assert any("Malformed JSON artifact" in error for error in publication.errors)


def test_malformed_gh_json_returns_structured_failure(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("gh", "pr", "view", "--json", "number,url,isDraft,baseRefName,headRefName"): publish_pr.CommandResult(
                0, "{", ""
            )
        }
    )
    publisher = publish_pr.Publisher(runner=runner)

    created, _number, _url, state, error = publisher.create_or_update_pr(tmp_path, "Title", "Body", "ready")

    assert created is False
    assert state == "not_created"
    assert "malformed gh JSON" in error


def test_two_run_ids_created_in_one_second_are_unique() -> None:
    first = publish_pr.make_run_id("issue-943")
    second = publish_pr.make_run_id("issue-943")

    assert first != second
    assert "issue-943" in first
    assert "." in first.split("Z", 1)[0]


def test_pr_comment_failure_warning_is_in_all_runtime_artifacts(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    state_file = install_fake_gh(tmp_path, monkeypatch)
    Path(str(state_file) + ".comment-fail").write_text("1", encoding="utf-8")
    root = prepare_e2e_root(tmp_path, repo, str(remote))

    publication = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert publication.execution_status == "completed"
    assert publication.pr_comment_posted is False
    assert any("PR publication comment failed" in warning for warning in publication.warnings)
    runtime = json.loads((Path(publication.run_dir) / "publication.json").read_text(encoding="utf-8"))
    summary = json.loads((Path(publication.run_dir) / "summary.json").read_text(encoding="utf-8"))
    tracked = json.loads((root / "artifacts" / "publication.json").read_text(encoding="utf-8"))
    assert runtime["warnings"] == publication.warnings
    assert summary["warnings"] == publication.warnings
    assert tracked["warnings"] == publication.warnings


def test_protected_main_branch_blocks_before_commit(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    payload_path = root / "artifacts" / "publication_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["branch"] = "main"
    payload_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    publication = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert publication.execution_status == "blocked"
    assert publication.commit_sha == ""
    assert any("protected branch" in error for error in publication.errors)


def test_publication_branch_policy_allows_requested_prefixes(tmp_path: Path) -> None:
    publisher = publish_pr.Publisher()
    for branch in ("feat/demo", "fix/demo", "issue/943", "tast/demo"):
        publication = publish_pr.PublicationResult()
        publisher.validate_publication_branch(branch, "main", publication, ["feat/", "fix/", "issue/", "tast/"])
        assert publication.errors == []


def test_publication_branch_policy_rejects_old_prefixes(tmp_path: Path) -> None:
    publisher = publish_pr.Publisher()
    old_prefixes = ("ta" + "sk/", "ag" + "ent/", "co" + "dex/")
    for branch in tuple(prefix + "demo" for prefix in old_prefixes):
        publication = publish_pr.PublicationResult()
        publisher.validate_publication_branch(branch, "main", publication, ["feat/", "fix/", "issue/", "tast/"])
        assert any("publication branch must start with one of" in error for error in publication.errors)


def test_default_publication_branch_uses_feat_prefix_for_non_issue_task(tmp_path: Path) -> None:
    publisher = publish_pr.Publisher()
    branch = publisher.publication_branch(tmp_path, {"task_id": "p2-hardening"}, {})
    assert branch == "feat/p2-hardening"


def test_completed_run_with_new_selected_diff_creates_new_commit_without_second_pr(
    tmp_path: Path, monkeypatch: object
) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    state_file = install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    first = publish_pr.Publisher(root=root).publish(repo_override=repo)
    assert first.execution_status == "completed"

    (repo / "allowed.txt").write_text("newer\n", encoding="utf-8")
    second = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert second.execution_status == "completed"
    assert second.commit_sha != first.commit_sha
    gh_log = Path(str(state_file) + ".log").read_text(encoding="utf-8")
    assert gh_log.count("pr create") == 1
    assert "pr edit 123" in gh_log


def test_blocked_before_commit_can_retry_after_condition_is_fixed(
    tmp_path: Path, monkeypatch: object
) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, "git@example.com:wrong/repo.git")
    first = publish_pr.Publisher(root=root).publish(repo_override=repo)
    assert first.execution_status == "blocked"
    assert first.commit_sha == ""

    change_set_path = root / "artifacts" / "change_set.json"
    change_set = json.loads(change_set_path.read_text(encoding="utf-8"))
    change_set["expected_remote"] = str(remote)
    change_set_path.write_text(json.dumps(change_set, indent=2) + "\n", encoding="utf-8")
    second = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert second.execution_status == "completed"
    assert not any("target repository remote does not match" in error for error in second.errors)


def test_resume_after_push_respects_current_await_approval_verdict(
    tmp_path: Path, monkeypatch: object
) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    state_file = install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    gh_path = tmp_path / "bin" / "gh"
    gh_path.write_text(
        gh_path.read_text(encoding="utf-8").replace(
            'if [ "$1" = "pr" ] && [ "$2" = "create" ]; then',
            'if [ "$1" = "pr" ] && [ "$2" = "create" ] && [ ! -f "' + str(state_file) + '.allow-create" ]; then\n  echo "api unavailable" >&2\n  exit 1\nfi\nif [ "$1" = "pr" ] && [ "$2" = "create" ]; then',
        ),
        encoding="utf-8",
    )
    first = publish_pr.Publisher(root=root).publish(repo_override=repo)
    assert first.push_completed is True
    assert first.pr_created_or_updated is False

    Path(str(state_file) + ".allow-create").write_text("1", encoding="utf-8")
    verdict_path = root / "artifacts" / "verdict.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["decision"] = "await_approval"
    verdict["approval_required_before_publish"] = True
    verdict["blockers"] = []
    verdict_path.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    second = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert second.execution_status == "blocked"
    assert second.pr_created_or_updated is False
    assert "Publication is not permitted by the current verdict or policy." in second.errors


def test_dry_run_scans_selected_secret_and_blocks(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    (repo / "allowed.txt").write_text("token='ghp_" + ("A" * 24) + "'\n", encoding="utf-8")
    install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts" / "security_scan.py", root / "scripts" / "security_scan.py")

    publication = publish_pr.Publisher(root=root).publish(dry_run=True, repo_override=repo)

    assert publication.execution_status == "blocked"
    assert publication.commit_sha == ""
    assert any("required security scan failed" in error for error in publication.errors)


def test_missing_origin_base_branch_blocks_publication(tmp_path: Path, monkeypatch: object) -> None:
    remote, repo, _ = init_publication_repo(tmp_path)
    install_fake_gh(tmp_path, monkeypatch)
    root = prepare_e2e_root(tmp_path, repo, str(remote))
    payload_path = root / "artifacts" / "publication_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["base_branch"] = "missing-base"
    payload_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    publication = publish_pr.Publisher(root=root).publish(repo_override=repo)

    assert publication.execution_status == "blocked"
    assert publication.commit_sha == ""
    assert "base branch origin/missing-base does not exist" in publication.errors


def test_optional_profile_commands_are_not_run(tmp_path: Path) -> None:
    runner = FakeRunner({("true",): publish_pr.CommandResult(0, "", "")})
    publisher = publish_pr.Publisher(runner=runner)
    publication = publish_pr.PublicationResult(pr_state="ready")
    profiles = {
        "profiles": {
            "agent_workspace": {
                "quality_commands": {
                    "required": ["true"],
                    "optional": ["false"],
                }
            }
        }
    }

    publisher.run_profile_commands(tmp_path, profiles, "agent_workspace", publication, "quality_commands")

    assert ("true",) in runner.calls
    assert ("false",) not in runner.calls
    assert publication.pr_state == "ready"
