#!/usr/bin/env python3
"""Autonomous commit, push, and PR publication executor."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
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
PUBLICATION_RESULT_START = "<!-- publication-result:start -->"
PUBLICATION_RESULT_END = "<!-- publication-result:end -->"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner:
    def __init__(self, timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, args: Sequence[str], cwd: Path) -> CommandResult:
        try:
            result = subprocess.run(
                list(args),
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            return CommandResult(127, "", str(exc))
        except subprocess.TimeoutExpired as exc:
            stderr = f"command timed out after {self.timeout_seconds}s"
            if exc.stderr:
                stderr = f"{stderr}: {exc.stderr}"
            return CommandResult(124, exc.stdout or "", stderr)
        except PermissionError as exc:
            return CommandResult(126, "", str(exc))
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
    run_id: str = ""
    run_dir: str = ""
    pr_comment_posted: bool = False
    command_results: list[dict[str, Any]] = field(default_factory=list)
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
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "pr_comment_posted": self.pr_comment_posted,
            "command_results": self.command_results,
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
    return any(path_matches_pattern(path, pattern) for pattern in patterns)


def path_matches_pattern(path: str, pattern: str) -> bool:
    normalized_path = path.strip("/")
    normalized_pattern = pattern.strip("/")
    if fnmatch.fnmatch(normalized_path, normalized_pattern):
        return True
    if normalized_pattern.startswith("**/") and fnmatch.fnmatch(
        normalized_path, normalized_pattern[3:]
    ):
        return True
    return False


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


def parse_pr_view_state(output: str) -> tuple[int, str, str]:
    data = json.loads(output)
    state = "draft" if data.get("isDraft") is True else "ready"
    return int(data.get("number", 0)), str(data.get("url", "")), state


def safe_relative_path(path: str) -> bool:
    value = Path(path)
    return bool(path) and not value.is_absolute() and ".." not in value.parts


def append_command_result(
    publication: PublicationResult,
    command: str,
    result: CommandResult,
    status: str,
) -> None:
    publication.command_results.append(
        {
            "command": command,
            "returncode": result.returncode,
            "status": status,
            "output": command_output(result),
        }
    )


def replace_marked_section(text: str, section: str) -> str:
    pattern = re.compile(
        rf"\n?{re.escape(PUBLICATION_RESULT_START)}.*?{re.escape(PUBLICATION_RESULT_END)}",
        re.DOTALL,
    )
    marked = f"\n{PUBLICATION_RESULT_START}\n{section.rstrip()}\n{PUBLICATION_RESULT_END}\n"
    if pattern.search(text):
        return pattern.sub(marked.rstrip(), text).rstrip() + "\n"
    return text.rstrip() + "\n" + marked


class Publisher:
    def __init__(self, root: Path = ROOT, runner: CommandRunner | None = None) -> None:
        self.root = root.resolve()
        self.artifacts = self.root / "artifacts"
        self.policy_path = self.root / ".agent-policy.yaml"
        self.project_profiles_path = self.root / ".agent-project-profiles.yaml"
        self.publication_path = self.artifacts / "publication.json"
        self.publication_payload_path = self.artifacts / "publication_payload.json"
        self.verdict_path = self.artifacts / "verdict.json"
        self.audit_log_path = self.artifacts / "audit_log.jsonl"
        self.runs_dir = self.root / ".agent-runs"
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
        expected_remote: str = "",
    ) -> PublicationResult:
        result = PublicationResult()
        profile = project_profile.get("project_profile", "")
        result.pr_state = determine_pr_state(quality, verdict)

        self.validate_orchestrator_decision(risk, verdict, result)
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
            append_command_result(result, "python3 scripts/validate_artifacts.py", validation, "required")
            if command_failed(validation):
                result.errors.append(f"artifact validation failed: {command_output(validation)}")
            security = self.run_command(
                ["python3", "scripts/security_scan.py", "--repo", str(target_repo), "--profile", profile],
                cwd=self.root,
            )
            append_command_result(
                result,
                f"python3 scripts/security_scan.py --repo {target_repo} --profile {profile}",
                security,
                "required",
            )
            if command_failed(security):
                result.errors.append(f"security scan failed: {command_output(security)}")
            self.run_profile_commands(target_repo, profiles_doc, profile, result, "quality_commands")
            self.run_profile_commands(target_repo, profiles_doc, profile, result, "security_commands")

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
        if expected_remote:
            actual_remote = self.run_command(["git", "remote", "get-url", "origin"], cwd=target_repo)
            if command_failed(actual_remote):
                result.errors.append(f"cannot read origin remote: {command_output(actual_remote)}")
            elif actual_remote.stdout.strip() != expected_remote:
                result.errors.append("target repository remote does not match expected_remote")

        gh_auth = self.run_command(["gh", "auth", "status"], cwd=target_repo)
        if command_failed(gh_auth):
            result.errors.append(f"gh auth status failed: {command_output(gh_auth)}")

        result.execution_status = "blocked" if result.errors else "running"
        return result

    def validate_orchestrator_decision(
        self,
        risk: dict[str, Any],
        verdict: dict[str, Any],
        publication: PublicationResult,
    ) -> None:
        if verdict.get("decision") != "publish_pr":
            publication.errors.append("verdict decision must be publish_pr")
        if verdict.get("approval_required_before_publish") is not False:
            publication.errors.append("approval_required_before_publish must be false")
        if verdict.get("blockers"):
            publication.errors.append("verdict blockers must be empty before publication")
        if verdict.get("execution_status") not in {"planned", "failed"}:
            publication.errors.append("verdict execution_status does not allow publication start")
        autonomy = risk.get("autonomy_allowed")
        if not isinstance(autonomy, dict):
            publication.errors.append("risk autonomy_allowed must be an object")
            return
        for field in ("commit", "push", "open_pr", "update_pr"):
            if autonomy.get(field) is not True:
                publication.errors.append(f"risk autonomy_allowed.{field} must be true")

    def run_profile_commands(
        self,
        target_repo: Path,
        profiles_doc: dict[str, Any],
        profile: str,
        publication: PublicationResult,
        command_group: str,
    ) -> None:
        profile_doc = profiles_doc.get("profiles", {}).get(profile, {})
        commands_doc = profile_doc.get(command_group, {})
        required_commands = commands_doc.get("required", [])
        if not isinstance(required_commands, list):
            publication.errors.append(f"profile {profile!r} has invalid required {command_group}")
            return
        for command in required_commands:
            if not isinstance(command, str):
                publication.errors.append(f"profile {profile!r} has non-string {command_group} command")
                continue
            result = self.run_command(shlex.split(command), cwd=target_repo)
            append_command_result(publication, command, result, "required")
            if command_failed(result):
                publication.warnings.append(
                    f"profile {command_group} command failed: {command}: {command_output(result)}"
                )
                publication.pr_state = "draft"

    def staged_files(self, target_repo: Path) -> set[str]:
        result = self.run_command(["git", "diff", "--cached", "--name-only"], cwd=target_repo)
        if command_failed(result):
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def tracked_file_exists(self, target_repo: Path, path: str) -> bool:
        result = self.run_command(["git", "ls-files", "--error-unmatch", "--", path], cwd=target_repo)
        return not command_failed(result)

    def changed_file_exists(self, target_repo: Path, path: str) -> bool:
        status = self.run_command(["git", "status", "--porcelain", "--", path], cwd=target_repo)
        return bool(status.stdout.strip()) if not command_failed(status) else False

    def selected_change_set_paths(
        self,
        target_repo: Path,
        change_set: dict[str, Any],
        publication: PublicationResult,
    ) -> set[str]:
        include = change_set.get("include", [])
        exclude = [path for path in change_set.get("exclude", []) if isinstance(path, str)]
        if not isinstance(include, list) or not include:
            publication.errors.append("change set include must be a non-empty list")
            return set()
        selected: set[str] = set()
        for path in include:
            if not isinstance(path, str) or not safe_relative_path(path):
                publication.errors.append(f"invalid change-set path: {path!r}")
                continue
            if matches_any(path, exclude):
                continue
            absolute = target_repo / path
            if not absolute.exists() and not self.tracked_file_exists(target_repo, path):
                publication.errors.append(f"change-set path is missing and untracked: {path}")
                continue
            if not self.changed_file_exists(target_repo, path):
                publication.errors.append(f"change-set path has no pending change: {path}")
                continue
            selected.add(path)
        if not selected and not publication.errors:
            publication.errors.append("change set selected no changed files")
        return selected

    def stage_change_set(
        self,
        target_repo: Path,
        change_set: dict[str, Any],
        dry_run: bool,
        publication: PublicationResult,
    ) -> set[str]:
        selected = self.selected_change_set_paths(target_repo, change_set, publication)
        if publication.errors:
            return set()
        pre_staged = self.staged_files(target_repo)
        unrelated_pre_staged = pre_staged - selected
        if unrelated_pre_staged:
            publication.errors.append(
                "pre-existing staged files outside change set: "
                + ", ".join(sorted(unrelated_pre_staged))
            )
            return set()
        if dry_run:
            return selected
        for path in sorted(selected):
            add_result = self.run_command(["git", "add", "--", path], cwd=target_repo)
            if command_failed(add_result):
                publication.errors.append(f"git add failed for {path}: {command_output(add_result)}")
        if publication.errors:
            return set()
        post_staged = self.staged_files(target_repo)
        if post_staged != selected:
            publication.errors.append(
                "staged files do not match change set: "
                f"expected {sorted(selected)}, got {sorted(post_staged)}"
            )
            return set()
        return selected

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
    ) -> tuple[bool, int, str, str, str]:
        view = self.run_command(["gh", "pr", "view", "--json", "number,url,isDraft"], cwd=target_repo)
        if not command_failed(view):
            number, url, actual_state = parse_pr_view_state(view.stdout)
            edit = self.run_command(["gh", "pr", "edit", "--title", title, "--body", body], cwd=target_repo)
            if command_failed(edit):
                return False, number, url, actual_state, command_output(edit)
            if pr_state == "ready" and actual_state == "draft":
                ready = self.run_command(["gh", "pr", "ready"], cwd=target_repo)
                if command_failed(ready):
                    return False, number, url, actual_state, command_output(ready)
            if pr_state == "draft" and actual_state == "ready":
                draft = self.run_command(["gh", "pr", "ready", "--undo"], cwd=target_repo)
                if command_failed(draft):
                    return False, number, url, actual_state, command_output(draft)
            final_view = self.run_command(["gh", "pr", "view", "--json", "number,url,isDraft"], cwd=target_repo)
            if command_failed(final_view):
                return False, number, url, actual_state, command_output(final_view)
            number, url, actual_state = parse_pr_view_state(final_view.stdout)
            if actual_state != pr_state:
                return False, number, url, actual_state, f"PR state is {actual_state}, expected {pr_state}"
            return True, number, url, actual_state, ""

        args = ["gh", "pr", "create", "--title", title, "--body", body]
        if pr_state == "draft":
            args.append("--draft")
        create = self.run_command(args, cwd=target_repo)
        if command_failed(create):
            return False, 0, "", "not_created", command_output(create)
        url = create.stdout.strip().splitlines()[-1]
        final_view = self.run_command(["gh", "pr", "view", "--json", "number,url,isDraft"], cwd=target_repo)
        if not command_failed(final_view):
            number, url, actual_state = parse_pr_view_state(final_view.stdout)
            if actual_state != pr_state:
                return False, number, url, actual_state, f"PR state is {actual_state}, expected {pr_state}"
            return True, number, url, actual_state, ""
        number = int(url.rstrip("/").split("/")[-1]) if url.rstrip("/").split("/")[-1].isdigit() else 0
        return True, number, url, pr_state, ""

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
        report_path.write_text(replace_marked_section(text, "\n".join(section)), encoding="utf-8")

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
        issue_path.write_text(replace_marked_section(text, "\n".join(section)), encoding="utf-8")

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

    def new_run_id(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    def runtime_markdown(self, publication: PublicationResult) -> str:
        lines = [
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
            lines.append(f"- Warnings: {'; '.join(publication.warnings)}")
        if publication.errors:
            lines.append(f"- Errors: {'; '.join(publication.errors)}")
        return "\n".join(lines) + "\n"

    def write_runtime_state(
        self,
        publication: PublicationResult,
        change_set: dict[str, Any],
        project_profile: str,
    ) -> Path:
        if not publication.run_id:
            publication.run_id = self.new_run_id()
        run_dir = self.runs_dir / publication.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        publication.run_dir = str(run_dir)
        write_json(run_dir / "publication.json", publication.as_json())
        write_json(
            run_dir / "summary.json",
            {
                "project_profile": project_profile,
                "task_id": change_set.get("task_id", ""),
                "target_repository": change_set.get("target_repository", ""),
                "publication": publication.as_json(),
            },
        )
        (run_dir / "publication.md").write_text(self.runtime_markdown(publication), encoding="utf-8")
        return run_dir

    def post_publication_comment(self, target_repo: Path, publication: PublicationResult) -> None:
        if not publication.pr_url:
            return
        body_path = Path(publication.run_dir) / "publication.md"
        comment = self.run_command(
            ["gh", "pr", "comment", publication.pr_url, "--body-file", str(body_path)],
            cwd=target_repo,
        )
        if command_failed(comment):
            publication.warnings.append(f"PR publication comment failed: {command_output(comment)}")
            return
        publication.pr_comment_posted = True
        write_json(Path(publication.run_dir) / "publication.json", publication.as_json())

    def record_runtime_publication(
        self,
        target_repo: Path,
        publication: PublicationResult,
        change_set: dict[str, Any],
        project_profile: str,
    ) -> None:
        self.write_runtime_state(publication, change_set, project_profile)
        self.post_publication_comment(target_repo, publication)

    def resolve_target_repo(
        self,
        change_set: dict[str, Any],
        repo_override: Path | None,
        publication: PublicationResult,
    ) -> Path:
        raw_path = str(repo_override) if repo_override is not None else str(change_set.get("target_repository", "."))
        if repo_override is None and Path(raw_path).is_absolute():
            publication.errors.append(
                "target_repository in artifact must be relative; use --repo for local absolute paths"
            )
        if repo_override is None and ".." in Path(raw_path).parts:
            publication.errors.append("target_repository in artifact must not contain '..'")
        target_repo = Path(raw_path).expanduser()
        if not target_repo.is_absolute():
            target_repo = self.root / target_repo
        target_repo = target_repo.resolve()
        if not target_repo.exists():
            publication.errors.append("target repository does not exist")
            return target_repo
        if not target_repo.is_dir():
            publication.errors.append("target repository is not a directory")
            return target_repo
        git_check = self.run_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=target_repo)
        if command_failed(git_check) or git_check.stdout.strip() != "true":
            publication.errors.append("target repository is not a git repository")
        return target_repo

    def load_publication_payload(self) -> dict[str, Any]:
        if not self.publication_payload_path.exists():
            return {}
        return read_json(self.publication_payload_path)

    def publish(
        self,
        dry_run: bool = False,
        skip_checks: bool = False,
        repo_override: Path | None = None,
    ) -> PublicationResult:
        if skip_checks and not (
            dry_run and os.environ.get("AGENT_HARNESS_TEST_MODE") == "1"
        ):
            result = PublicationResult(execution_status="blocked", dry_run=dry_run)
            result.errors.append("--skip-checks is only allowed with --dry-run in AGENT_HARNESS_TEST_MODE=1")
            return result
        policy = read_yaml(self.policy_path)
        profiles_doc = read_yaml(self.project_profiles_path)
        risk = read_json(self.artifacts / "risk.json")
        quality = read_json(self.artifacts / "quality.json")
        verdict = read_json(self.artifacts / "verdict.json")
        project_profile = read_json(self.artifacts / "project_profile.json")
        change_set = read_json(self.artifacts / "change_set.json")
        publication_payload = self.load_publication_payload()
        publication = PublicationResult(dry_run=dry_run)
        target_repo = self.resolve_target_repo(change_set, repo_override, publication)
        if publication.errors:
            publication.execution_status = "blocked"
            if not dry_run:
                self.record_runtime_publication(
                    target_repo,
                    publication,
                    change_set,
                    str(project_profile.get("project_profile", "")),
                )
            return publication

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
            str(change_set.get("expected_remote", "")),
        )
        publication.dry_run = dry_run
        title = str(publication_payload.get("title", change_set.get("task_id", "Task changes")))
        body = str(publication_payload.get("body", ""))
        commit_message = str(publication_payload.get("commit_message", title))
        publication.errors.extend(
            forbidden_public_output_blockers(policy, publication.branch, title, body + "\n" + commit_message)
            if project_profile.get("project_profile") == "flowfox"
            else []
        )
        if publication.errors:
            publication.execution_status = "blocked"
            if not dry_run:
                self.record_runtime_publication(
                    target_repo,
                    publication,
                    change_set,
                    str(project_profile.get("project_profile", "")),
                )
            return publication

        staged = self.stage_change_set(target_repo, change_set, dry_run, publication)
        if not staged:
            if not publication.errors:
                publication.errors.append("change set selected no files to stage")
            publication.execution_status = "blocked"
        elif dry_run:
            publication.execution_status = "planned"
            publication.warnings.append("dry-run: no files staged, committed, pushed, or published")
        else:
            if self.has_staged_changes(target_repo):
                commit_created, sha, error = self.commit(target_repo, commit_message)
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
                created, number, url, actual_state, error = self.create_or_update_pr(
                    target_repo,
                    title,
                    body,
                    publication.pr_state,
                )
                publication.pr_created_or_updated = created
                publication.pr_number = number
                publication.pr_url = url
                publication.pr_state = actual_state
                if error:
                    publication.errors.append(f"PR publication failed: {error}")
                    publication.execution_status = "failed"
                else:
                    publication.execution_status = "completed"

        if not dry_run:
            self.record_runtime_publication(
                target_repo,
                publication,
                change_set,
                str(project_profile.get("project_profile", "")),
            )
        return publication


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate and plan without mutations.")
    parser.add_argument("--repo", type=Path, default=None, help="Override target repository path.")
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Test-only bypass. Requires --dry-run and AGENT_HARNESS_TEST_MODE=1.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    publication = Publisher().publish(
        dry_run=args.dry_run,
        skip_checks=args.skip_checks,
        repo_override=args.repo,
    )
    print(json.dumps(publication.as_json(), indent=2, ensure_ascii=False))
    if publication.errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
