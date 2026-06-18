#!/usr/bin/env python3
"""Autonomous commit, push, and PR publication executor."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
POLICY = ROOT / ".agent-policy.yaml"
PROJECT_PROFILES = ROOT / ".agent-project-profiles.yaml"
PUBLICATION = ARTIFACTS / "publication.json"
VERDICT = ARTIFACTS / "verdict.json"
AUDIT_LOG = ARTIFACTS / "audit_log.jsonl"


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner:
    def run(self, args: Sequence[str], cwd: Path) -> CommandResult:
        result = subprocess.run(
            list(args),
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr)


@dataclass
class PublicationResult:
    execution_status: str = "planned"
    branch: str = ""
    commit_created: bool = False
    commit_sha: str = ""
    branch_pushed: bool = False
    pr_created_or_updated: bool = False
    pr_number: int = 0
    pr_url: str = ""
    pr_state: str = "not_created"
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "execution_status": self.execution_status,
            "branch": self.branch,
            "commit_created": self.commit_created,
            "commit_sha": self.commit_sha,
            "branch_pushed": self.branch_pushed,
            "pr_created_or_updated": self.pr_created_or_updated,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "pr_state": self.pr_state,
            "dry_run": self.dry_run,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def command_failed(result: CommandResult) -> bool:
    return result.returncode != 0


def command_output(result: CommandResult) -> str:
    return (result.stdout + result.stderr).strip()


def matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def determine_pr_state(quality: dict[str, Any], verdict: dict[str, Any]) -> str:
    evidence = verdict.get("flowfox_visual_evidence", {})
    missing_evidence = (
        isinstance(evidence, dict)
        and evidence.get("required") is True
        and evidence.get("provided") is not True
    )
    checks_passed = (
        quality.get("overall_status") == "pass"
        and quality.get("focused_tests_passed") is True
        and quality.get("repository_checks_passed") is True
        and verdict.get("checks_passed") is True
    )
    if checks_passed and not missing_evidence:
        return "ready"
    return "draft"


def protected_path_blockers(
    change_set: dict[str, Any],
    policy: dict[str, Any],
    project_profile: str,
) -> list[str]:
    if project_profile == "agent_workspace":
        return []
    flowfox = policy.get("projects", {}).get("flowfox", {})
    protected = flowfox.get("protected_paths", [])
    if not isinstance(protected, list):
        return ["policy missing Flowfox protected_paths"]
    return [
        path
        for path in change_set.get("include", [])
        if isinstance(path, str) and matches_any(path, protected)
    ]


def forbidden_public_output_blockers(
    policy: dict[str, Any],
    branch: str,
    title: str,
    body: str,
) -> list[str]:
    flowfox = policy.get("projects", {}).get("flowfox", {})
    phrases = flowfox.get("public_output_forbidden_phrases", [])
    if not isinstance(phrases, list):
        return ["policy missing public_output_forbidden_phrases"]
    public_text = "\n".join([branch, title, body])
    return [f"public output contains forbidden phrase: {phrase}" for phrase in phrases if phrase in public_text]


def parse_pr_view(output: str) -> tuple[int, str]:
    data = json.loads(output)
    return int(data.get("number", 0)), str(data.get("url", ""))


class Publisher:
    def __init__(self, root: Path = ROOT, runner: CommandRunner | None = None) -> None:
        self.root = root.resolve()
        self.artifacts = self.root / "artifacts"
        self.policy_path = self.root / ".agent-policy.yaml"
        self.project_profiles_path = self.root / ".agent-project-profiles.yaml"
        self.publication_path = self.artifacts / "publication.json"
        self.verdict_path = self.artifacts / "verdict.json"
        self.audit_log_path = self.artifacts / "audit_log.jsonl"
        self.runner = runner or CommandRunner()

    def run_command(self, args: Sequence[str], cwd: Path | None = None) -> CommandResult:
        return self.runner.run(args, cwd or self.root)

    def preflight(
        self,
        target_repo: Path,
        policy: dict[str, Any],
        profiles_doc: dict[str, Any],
        risk: dict[str, Any],
        quality: dict[str, Any],
        verdict: dict[str, Any],
        project_profile: dict[str, Any],
        change_set: dict[str, Any],
        skip_checks: bool,
    ) -> PublicationResult:
        result = PublicationResult()
        profile = project_profile.get("project_profile", "")
        result.pr_state = determine_pr_state(quality, verdict)

        if change_set.get("project_profile") != profile:
            result.errors.append("change_set project_profile does not match project_profile artifact")
        if risk.get("risk_class") == "high":
            result.errors.append("HIGH risk blocks autonomous publication")
        if risk.get("high_risk_triggers") or verdict.get("high_risk_triggers"):
            result.errors.append("high-risk triggers block autonomous publication")
        if risk.get("protected_paths_touched") or verdict.get("protected_paths_touched"):
            result.errors.append("protected_paths_touched blocks autonomous publication")
        for protected_path in protected_path_blockers(change_set, policy, profile):
            result.errors.append(f"protected path in change set: {protected_path}")

        if not skip_checks:
            validation = self.run_command(["python3", "scripts/validate_artifacts.py"], cwd=self.root)
            if command_failed(validation):
                result.errors.append(f"artifact validation failed: {command_output(validation)}")
            security = self.run_command(
                ["python3", "scripts/security_scan.py", "--repo", str(target_repo), "--profile", profile],
                cwd=self.root,
            )
            if command_failed(security):
                result.errors.append(f"security scan failed: {command_output(security)}")
            self.run_profile_quality_checks(target_repo, profiles_doc, profile, result)

        branch = self.run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=target_repo)
        if command_failed(branch):
            result.errors.append(f"cannot determine git branch: {command_output(branch)}")
        else:
            result.branch = branch.stdout.strip()
            if result.branch == "HEAD":
                result.errors.append("detached HEAD blocks publication")
            if result.branch in {"main", "master", "trunk"}:
                result.errors.append(f"default branch {result.branch!r} blocks publication")

        unmerged = self.run_command(["git", "diff", "--name-only", "--diff-filter=U"], cwd=target_repo)
        if command_failed(unmerged):
            result.errors.append(f"cannot inspect merge conflicts: {command_output(unmerged)}")
        elif unmerged.stdout.strip():
            result.errors.append("merge or rebase conflict blocks publication")

        name = self.run_command(["git", "config", "user.name"], cwd=target_repo)
        email = self.run_command(["git", "config", "user.email"], cwd=target_repo)
        if command_failed(name) or not name.stdout.strip() or command_failed(email) or not email.stdout.strip():
            result.errors.append("missing git identity blocks publication")

        remote = self.run_command(["git", "remote"], cwd=target_repo)
        if command_failed(remote) or not remote.stdout.strip():
            result.errors.append("missing git remote blocks publication")

        gh_auth = self.run_command(["gh", "auth", "status"], cwd=target_repo)
        if command_failed(gh_auth):
            result.errors.append(f"gh auth status failed: {command_output(gh_auth)}")

        result.execution_status = "blocked" if result.errors else "running"
        return result

    def run_profile_quality_checks(
        self,
        target_repo: Path,
        profiles_doc: dict[str, Any],
        profile: str,
        publication: PublicationResult,
    ) -> None:
        profile_doc = profiles_doc.get("profiles", {}).get(profile, {})
        quality_commands = profile_doc.get("quality_commands", {})
        required_commands = quality_commands.get("required", [])
        if not isinstance(required_commands, list):
            publication.errors.append(f"profile {profile!r} has invalid required quality commands")
            return
        for command in required_commands:
            if not isinstance(command, str):
                publication.errors.append(f"profile {profile!r} has non-string quality command")
                continue
            result = self.run_command(shlex.split(command), cwd=target_repo)
            if command_failed(result):
                publication.warnings.append(
                    f"profile quality command failed: {command}: {command_output(result)}"
                )
                publication.pr_state = "draft"

    def stage_change_set(self, target_repo: Path, change_set: dict[str, Any], dry_run: bool) -> list[str]:
        include = [path for path in change_set.get("include", []) if isinstance(path, str)]
        exclude = [path for path in change_set.get("exclude", []) if isinstance(path, str)]
        staged = [path for path in include if not matches_any(path, exclude)]
        if dry_run:
            return staged
        for path in staged:
            self.run_command(["git", "add", "--", path], cwd=target_repo)
        return staged

    def has_staged_changes(self, target_repo: Path) -> bool:
        result = self.run_command(["git", "diff", "--cached", "--quiet"], cwd=target_repo)
        return result.returncode == 1

    def commit(self, target_repo: Path, message: str) -> tuple[bool, str, str]:
        commit_result = self.run_command(["git", "commit", "-m", message], cwd=target_repo)
        if command_failed(commit_result):
            return False, "", command_output(commit_result)
        sha = self.run_command(["git", "rev-parse", "HEAD"], cwd=target_repo)
        if command_failed(sha):
            return True, "", command_output(sha)
        return True, sha.stdout.strip(), ""

    def push(self, target_repo: Path, branch: str) -> str:
        push_result = self.run_command(["git", "push", "--set-upstream", "origin", branch], cwd=target_repo)
        return "" if not command_failed(push_result) else command_output(push_result)

    def create_or_update_pr(
        self,
        target_repo: Path,
        title: str,
        body: str,
        pr_state: str,
    ) -> tuple[bool, int, str, str]:
        view = self.run_command(["gh", "pr", "view", "--json", "number,url"], cwd=target_repo)
        if not command_failed(view):
            number, url = parse_pr_view(view.stdout)
            edit = self.run_command(["gh", "pr", "edit", "--title", title, "--body", body], cwd=target_repo)
            if command_failed(edit):
                return False, number, url, command_output(edit)
            if pr_state == "ready":
                self.run_command(["gh", "pr", "ready"], cwd=target_repo)
            return True, number, url, ""

        args = ["gh", "pr", "create", "--title", title, "--body", body]
        if pr_state == "draft":
            args.append("--draft")
        create = self.run_command(args, cwd=target_repo)
        if command_failed(create):
            return False, 0, "", command_output(create)
        url = create.stdout.strip().splitlines()[-1]
        number = int(url.rstrip("/").split("/")[-1]) if url.rstrip("/").split("/")[-1].isdigit() else 0
        return True, number, url, ""

    def update_artifacts(self, publication: PublicationResult) -> None:
        write_json(self.publication_path, publication.as_json())
        verdict = read_json(self.verdict_path)
        verdict["execution_status"] = publication.execution_status
        verdict["publication_result"] = {
            "commit_created": publication.commit_created,
            "branch_pushed": publication.branch_pushed,
            "pr_created_or_updated": publication.pr_created_or_updated,
            "pr_url": publication.pr_url,
            "pr_state": publication.pr_state,
        }
        if publication.errors:
            verdict["blockers"] = publication.errors
        verdict["warnings"] = sorted(set(verdict.get("warnings", []) + publication.warnings))
        write_json(self.verdict_path, verdict)

    def append_audit_log(self, publication: PublicationResult) -> None:
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "agent": "codex",
            "action": "publish-pr-executor",
            "commit_sha": publication.commit_sha,
            "verdict": publication.execution_status,
            "checks_passed": not publication.errors,
            "project_profile": read_json(self.artifacts / "project_profile.json").get("project_profile"),
            "dry_run": publication.dry_run,
        }
        with self.audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def update_report(self, publication: PublicationResult) -> None:
        report_path = self.artifacts / "report.md"
        if not report_path.exists():
            return
        text = report_path.read_text(encoding="utf-8").rstrip()
        section = [
            "",
            "## Publication Result",
            "",
            f"- Execution status: `{publication.execution_status}`",
            f"- Branch: `{publication.branch}`",
            f"- Commit created: `{publication.commit_created}`",
            f"- Commit SHA: `{publication.commit_sha}`",
            f"- Branch pushed: `{publication.branch_pushed}`",
            f"- PR created or updated: `{publication.pr_created_or_updated}`",
            f"- PR URL: `{publication.pr_url}`",
            f"- PR state: `{publication.pr_state}`",
        ]
        if publication.warnings:
            section.append(f"- Warnings: {'; '.join(publication.warnings)}")
        if publication.errors:
            section.append(f"- Errors: {'; '.join(publication.errors)}")
        report_path.write_text(text + "\n" + "\n".join(section) + "\n", encoding="utf-8")

    def update_issue_journal(
        self,
        change_set: dict[str, Any],
        project_profile: str,
        publication: PublicationResult,
    ) -> None:
        if project_profile != "flowfox":
            return
        task_id = str(change_set.get("task_id", ""))
        match = re.search(r"issue-(\d+)", task_id)
        if match is None:
            return
        issue_path = self.root / "docs" / "projects" / "flowfox" / "issues" / f"issue-{match.group(1)}.md"
        if not issue_path.exists():
            return
        text = issue_path.read_text(encoding="utf-8").rstrip()
        section = [
            "",
            "## Publication Result",
            "",
            f"- Execution status: `{publication.execution_status}`",
            f"- Branch: `{publication.branch}`",
            f"- Commit SHA: `{publication.commit_sha}`",
            f"- PR URL: `{publication.pr_url}`",
            f"- PR state: `{publication.pr_state}`",
        ]
        issue_path.write_text(text + "\n" + "\n".join(section) + "\n", encoding="utf-8")

    def record_publication(
        self,
        publication: PublicationResult,
        change_set: dict[str, Any],
        project_profile: str,
    ) -> None:
        self.update_artifacts(publication)
        self.update_report(publication)
        self.update_issue_journal(change_set, project_profile, publication)
        self.append_audit_log(publication)

    def publish(self, dry_run: bool = False, skip_checks: bool = False) -> PublicationResult:
        policy = read_yaml(self.policy_path)
        profiles_doc = read_yaml(self.project_profiles_path)
        risk = read_json(self.artifacts / "risk.json")
        quality = read_json(self.artifacts / "quality.json")
        verdict = read_json(self.artifacts / "verdict.json")
        project_profile = read_json(self.artifacts / "project_profile.json")
        change_set = read_json(self.artifacts / "change_set.json")
        target_repo = Path(change_set["target_repository"]).expanduser().resolve()

        publication = self.preflight(
            target_repo,
            policy,
            profiles_doc,
            risk,
            quality,
            verdict,
            project_profile,
            change_set,
            skip_checks,
        )
        publication.dry_run = dry_run
        title = f"{change_set.get('task_id', 'Task changes')}"
        body = (ARTIFACTS / "report.md").read_text(encoding="utf-8") if (ARTIFACTS / "report.md").exists() else ""
        publication.errors.extend(
            forbidden_public_output_blockers(policy, publication.branch, title, body)
            if project_profile.get("project_profile") == "flowfox"
            else []
        )
        if publication.errors:
            publication.execution_status = "blocked"
            if not dry_run:
                self.record_publication(publication, change_set, str(project_profile.get("project_profile", "")))
            return publication

        staged = self.stage_change_set(target_repo, change_set, dry_run)
        if not staged:
            publication.errors.append("change set selected no files to stage")
            publication.execution_status = "blocked"
        elif dry_run:
            publication.execution_status = "planned"
            publication.warnings.append("dry-run: no files staged, committed, pushed, or published")
        else:
            if self.has_staged_changes(target_repo):
                commit_created, sha, error = self.commit(target_repo, title)
                publication.commit_created = commit_created
                publication.commit_sha = sha
                if error:
                    publication.errors.append(f"commit failed: {error}")
                    publication.execution_status = "failed"
            else:
                existing = read_json(self.publication_path) if self.publication_path.exists() else {}
                publication.commit_created = bool(existing.get("commit_created"))
                publication.commit_sha = str(existing.get("commit_sha", ""))
                if not publication.commit_created:
                    publication.errors.append("no staged changes and no prior publication commit")
                    publication.execution_status = "blocked"

            if publication.commit_created and not publication.errors:
                push_error = self.push(target_repo, publication.branch)
                if push_error:
                    publication.errors.append(f"push failed: {push_error}")
                    publication.execution_status = "failed"
                else:
                    publication.branch_pushed = True

            if publication.branch_pushed and not publication.errors:
                created, number, url, error = self.create_or_update_pr(
                    target_repo,
                    title,
                    body,
                    publication.pr_state,
                )
                publication.pr_created_or_updated = created
                publication.pr_number = number
                publication.pr_url = url
                if error:
                    publication.errors.append(f"PR publication failed: {error}")
                    publication.execution_status = "failed"
                else:
                    publication.execution_status = "completed"

        if not dry_run:
            self.record_publication(publication, change_set, str(project_profile.get("project_profile", "")))
        return publication


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate and plan without mutations.")
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip artifact/security checks. Intended for focused tests only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    publication = Publisher().publish(dry_run=args.dry_run, skip_checks=args.skip_checks)
    print(json.dumps(publication.as_json(), indent=2, ensure_ascii=False))
    if publication.errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
