#!/usr/bin/env python3
"""Safe, resumable commit, push, and PR publication executor."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import random
import re
import shlex
import shutil
import string
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
PUBLICATION_RESULT_START = "<!-- publication-result:start -->"
PUBLICATION_RESULT_END = "<!-- publication-result:end -->"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
DEFAULT_BASE_BRANCH = "main"
FINAL_STATES = {"completed", "blocked", "failed"}
PUBLICATION_STATES = {
    "planned",
    "preflight_passed",
    "staged",
    "committed",
    "pushed",
    "pr_published",
    "completed",
    "blocked",
    "failed",
}


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
    target_repository: str = ""
    worktree: str = ""
    branch: str = ""
    base_branch: str = DEFAULT_BASE_BRANCH
    commit_created: bool = False
    commit_sha: str = ""
    branch_pushed: bool = False
    push_completed: bool = False
    pr_created_or_updated: bool = False
    pr_number: int = 0
    pr_url: str = ""
    pr_state: str = "not_created"
    dry_run: bool = False
    run_id: str = ""
    run_dir: str = ""
    task_id: str = ""
    pr_comment_posted: bool = False
    command_results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "execution_status": self.execution_status,
            "target_repository": self.target_repository,
            "worktree": self.worktree,
            "branch": self.branch,
            "base_branch": self.base_branch,
            "commit_created": self.commit_created,
            "commit_sha": self.commit_sha,
            "branch_pushed": self.branch_pushed,
            "push_completed": self.push_completed,
            "pr_created_or_updated": self.pr_created_or_updated,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "pr_state": self.pr_state,
            "dry_run": self.dry_run,
            "run_dir": self.run_dir,
            "pr_comment_posted": self.pr_comment_posted,
            "command_results": self.command_results,
            "warnings": self.warnings,
            "errors": self.errors,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PublicationResult":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        values = {key: value for key, value in data.items() if key in allowed}
        result = cls(**values)
        result.push_completed = bool(data.get("push_completed", data.get("branch_pushed", False)))
        result.branch_pushed = bool(data.get("branch_pushed", result.push_completed))
        return result


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a YAML object")
    return data


def command_failed(result: CommandResult) -> bool:
    return result.returncode != 0


def command_output(result: CommandResult) -> str:
    return (result.stdout + result.stderr).strip()


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


def path_matches_pattern(path: str, pattern: str) -> bool:
    normalized_path = path.strip("/")
    normalized_pattern = pattern.strip("/")
    if fnmatch.fnmatch(normalized_path, normalized_pattern):
        return True
    if normalized_pattern.startswith("**/") and fnmatch.fnmatch(normalized_path, normalized_pattern[3:]):
        return True
    return False


def matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(path_matches_pattern(path, pattern) for pattern in patterns)


def safe_relative_path(path: str) -> bool:
    value = Path(path)
    return bool(path) and not value.is_absolute() and ".." not in value.parts


def sanitize_slug(value: str, fallback: str = "task") -> str:
    slug = re.sub(r"[^A-Za-z0-9._/-]+", "-", value.strip()).strip("-./")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or fallback


def random_suffix(length: int = 6) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.SystemRandom().choice(alphabet) for _ in range(length))


def make_run_id(task_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{timestamp}-{sanitize_slug(task_id)}-{random_suffix()}"


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


def parse_pr_view_state(output: str) -> tuple[int, str, str, str, str]:
    data = json.loads(output)
    state = "draft" if data.get("isDraft") is True else "ready"
    return (
        int(data.get("number", 0)),
        str(data.get("url", "")),
        state,
        str(data.get("baseRefName", "")),
        str(data.get("headRefName", "")),
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
        self.worktrees_dir = self.root / ".agent-worktrees"
        self.runner = runner or CommandRunner()

    def run_command(self, args: Sequence[str], cwd: Path | None = None) -> CommandResult:
        return self.runner.run(args, cwd or self.root)

    def structured_blocked(self, message: str, dry_run: bool = False) -> PublicationResult:
        return PublicationResult(execution_status="blocked", dry_run=dry_run, errors=[message])

    def load_inputs(self) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        return (
            read_yaml(self.policy_path),
            read_yaml(self.project_profiles_path),
            read_json(self.artifacts / "risk.json"),
            read_json(self.artifacts / "quality.json"),
            read_json(self.artifacts / "verdict.json"),
            read_json(self.artifacts / "project_profile.json"),
            read_json(self.artifacts / "change_set.json"),
        )

    def load_publication_payload(self) -> dict[str, Any]:
        if not self.publication_payload_path.exists():
            return {}
        return read_json(self.publication_payload_path)

    def resolve_target_repo(
        self,
        change_set: dict[str, Any],
        repo_override: Path | None,
        publication: PublicationResult,
    ) -> Path:
        raw_path = str(repo_override) if repo_override is not None else str(change_set.get("target_repository", "."))
        if repo_override is None and Path(raw_path).is_absolute():
            publication.errors.append("target_repository in artifact must be relative; use --repo for local absolute paths")
        if repo_override is None and ".." in Path(raw_path).parts:
            publication.errors.append("target_repository in artifact must not contain '..'")
        target_repo = Path(raw_path).expanduser()
        if not target_repo.is_absolute():
            target_repo = self.root / target_repo
        target_repo = target_repo.resolve()
        publication.target_repository = str(target_repo)
        if not target_repo.exists():
            publication.errors.append("Target repository does not exist.")
            return target_repo
        if not target_repo.is_dir():
            publication.errors.append("Target repository is not a directory.")
            return target_repo
        git_check = self.run_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=target_repo)
        if command_failed(git_check) or git_check.stdout.strip() != "true":
            publication.errors.append("Target repository is not a git repository.")
        return target_repo

    def current_branch(self, repo: Path) -> str:
        result = self.run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
        return result.stdout.strip() if not command_failed(result) else ""

    def publication_branch(self, target_repo: Path, change_set: dict[str, Any], payload: dict[str, Any]) -> str:
        explicit = payload.get("branch") or change_set.get("branch")
        if isinstance(explicit, str) and explicit.strip():
            return sanitize_slug(explicit)
        task_id = sanitize_slug(str(change_set.get("task_id", "task")))
        return f"issue/{task_id.removeprefix('issue-')}" if task_id.startswith("issue-") else f"task/{task_id}"

    def selected_change_set_paths(
        self,
        target_repo: Path,
        change_set: dict[str, Any],
        publication: PublicationResult,
        require_pending_change: bool = True,
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
            if require_pending_change and not self.changed_file_exists(target_repo, path):
                publication.errors.append(f"change-set path has no pending change: {path}")
                continue
            selected.add(path)
        if not selected and not publication.errors:
            publication.errors.append("change set selected no changed files")
        return selected

    def tracked_file_exists(self, target_repo: Path, path: str) -> bool:
        result = self.run_command(["git", "ls-files", "--error-unmatch", "--", path], cwd=target_repo)
        return not command_failed(result)

    def changed_file_exists(self, target_repo: Path, path: str) -> bool:
        status = self.run_command(["git", "status", "--porcelain", "--", path], cwd=target_repo)
        return bool(status.stdout.strip()) if not command_failed(status) else False

    def staged_files(self, target_repo: Path) -> set[str]:
        result = self.run_command(["git", "diff", "--cached", "--name-only"], cwd=target_repo)
        if command_failed(result):
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def stage_change_set(
        self,
        target_repo: Path,
        change_set: dict[str, Any],
        dry_run: bool,
        publication: PublicationResult,
        require_pending_change: bool = True,
    ) -> set[str]:
        selected = self.selected_change_set_paths(target_repo, change_set, publication, require_pending_change)
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
        publication.execution_status = "staged"
        return selected

    def validate_orchestrator_decision(
        self,
        risk: dict[str, Any],
        verdict: dict[str, Any],
        publication: PublicationResult,
    ) -> None:
        permitted = (
            verdict.get("decision") == "publish_pr"
            and risk.get("risk_class") != "high"
            and verdict.get("approval_required_before_publish") is False
            and verdict.get("blockers") == []
            and isinstance(risk.get("autonomy_allowed"), dict)
            and risk["autonomy_allowed"].get("commit") is True
            and risk["autonomy_allowed"].get("push") is True
            and risk["autonomy_allowed"].get("open_pr") is True
            and risk["autonomy_allowed"].get("update_pr") is True
        )
        if not permitted:
            publication.errors.append("Publication is not permitted by the current verdict or policy.")
        if risk.get("high_risk_triggers") or verdict.get("high_risk_triggers"):
            publication.errors.append("high-risk triggers block autonomous publication")
        if risk.get("protected_paths_touched") or verdict.get("protected_paths_touched"):
            publication.errors.append("protected_paths_touched blocks autonomous publication")

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
        result = PublicationResult(target_repository=str(target_repo))
        profile = str(project_profile.get("project_profile", ""))
        result.task_id = str(change_set.get("task_id", ""))
        result.pr_state = determine_pr_state(quality, verdict)
        self.validate_orchestrator_decision(risk, verdict, result)
        if change_set.get("project_profile") != profile:
            result.errors.append("change_set project_profile does not match project_profile artifact")
        for protected_path in protected_path_blockers(change_set, policy, profile):
            result.errors.append(f"protected path in change set: {protected_path}")

        if not skip_checks:
            validation = self.run_command(["python3", "scripts/validate_artifacts.py"], cwd=self.root)
            append_command_result(result, "python3 scripts/validate_artifacts.py", validation, "required")
            if command_failed(validation):
                result.errors.append(f"artifact validation failed: {command_output(validation)}")

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

        result.execution_status = "blocked" if result.errors else "preflight_passed"
        return result

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
        if not isinstance(commands_doc, dict):
            publication.errors.append(f"profile {profile!r} has invalid {command_group}")
            return
        for status in ("required", "optional"):
            commands = commands_doc.get(status, [])
            if not isinstance(commands, list):
                publication.errors.append(f"profile {profile!r} has invalid {status} {command_group}")
                continue
            for command in commands:
                if not isinstance(command, str):
                    publication.errors.append(f"profile {profile!r} has non-string {command_group} command")
                    continue
                result = self.run_command(shlex.split(command), cwd=target_repo)
                append_command_result(publication, command, result, status)
                if not command_failed(result):
                    continue
                output = command_output(result)
                if command_group == "security_commands" and status == "required":
                    publication.errors.append(f"required security command failed: {command}: {output}")
                elif command_group == "security_commands":
                    publication.warnings.append(f"optional security command unavailable or failed: {command}: {output}")
                else:
                    publication.warnings.append(f"profile {command_group} command failed: {command}: {output}")
                    publication.pr_state = "draft"

    def write_selected_paths_file(self, run_dir: Path, selected: set[str]) -> Path:
        path = run_dir / "selected-paths.txt"
        path.write_text("\n".join(sorted(selected)) + "\n", encoding="utf-8")
        return path

    def run_selected_security_scan(
        self,
        worktree: Path,
        profile: str,
        selected: set[str],
        publication: PublicationResult,
    ) -> None:
        paths_file = self.write_selected_paths_file(Path(publication.run_dir), selected)
        command = [
            "python3",
            "scripts/security_scan.py",
            "--repo",
            str(worktree),
            "--profile",
            profile,
            "--paths-file",
            str(paths_file),
        ]
        result = self.run_command(command, cwd=self.root)
        append_command_result(publication, " ".join(shlex.quote(part) for part in command), result, "required")
        if command_failed(result):
            publication.errors.append(f"required security scan failed: {command_output(result)}")

    def source_ref(self, target_repo: Path, base_branch: str) -> str:
        for ref in (f"origin/{base_branch}", base_branch):
            check = self.run_command(["git", "rev-parse", "--verify", ref], cwd=target_repo)
            if not command_failed(check):
                return ref
        return "HEAD"

    def create_worktree(
        self,
        target_repo: Path,
        change_set: dict[str, Any],
        publication: PublicationResult,
    ) -> Path:
        if publication.worktree:
            worktree = Path(publication.worktree)
            if worktree.exists():
                return worktree
        task_slug = sanitize_slug(str(change_set.get("task_id", "task")))
        suffix = publication.run_id.rsplit("-", 1)[-1] if publication.run_id else random_suffix()
        worktree = self.worktrees_dir / f"{task_slug}-{suffix}"
        publication.worktree = str(worktree.resolve())
        if worktree.exists():
            return worktree.resolve()
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        base_ref = self.source_ref(target_repo, publication.base_branch)
        result = self.run_command(
            ["git", "worktree", "add", "-b", publication.branch, str(worktree), base_ref],
            cwd=target_repo,
        )
        if command_failed(result):
            fallback = self.run_command(["git", "worktree", "add", str(worktree), publication.branch], cwd=target_repo)
            if command_failed(fallback):
                publication.errors.append(f"git worktree add failed: {command_output(result) or command_output(fallback)}")
        return worktree.resolve()

    def copy_selected_changes(
        self,
        source_repo: Path,
        worktree: Path,
        selected: set[str],
        publication: PublicationResult,
    ) -> None:
        for relative in sorted(selected):
            source = source_repo / relative
            destination = worktree / relative
            try:
                if source.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if source.is_dir():
                        if destination.exists():
                            shutil.rmtree(destination)
                        shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))
                    else:
                        shutil.copy2(source, destination)
                elif destination.exists():
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    else:
                        destination.unlink()
            except OSError as exc:
                publication.errors.append(f"failed to copy selected change {relative}: {exc}")

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
        base_branch: str = DEFAULT_BASE_BRANCH,
        branch: str = "",
        body_file: Path | None = None,
    ) -> tuple[bool, int, str, str, str]:
        view_args = ["gh", "pr", "view", "--json", "number,url,isDraft,baseRefName,headRefName"]
        view = self.run_command(view_args, cwd=target_repo)
        if not command_failed(view):
            try:
                number, url, actual_state, actual_base, _head = parse_pr_view_state(view.stdout)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                return False, 0, "", "not_created", f"malformed gh JSON: {exc}"
            edit_args = ["gh", "pr", "edit", str(number), "--title", title]
            if body_file is not None:
                edit_args.extend(["--body-file", str(body_file)])
            else:
                edit_args.extend(["--body", body])
            if actual_base and actual_base != base_branch:
                edit_args.extend(["--base", base_branch])
            edit = self.run_command(edit_args, cwd=target_repo)
            if command_failed(edit):
                return False, number, url, actual_state, command_output(edit)
            if pr_state == "ready" and actual_state == "draft":
                ready = self.run_command(["gh", "pr", "ready", str(number)], cwd=target_repo)
                if command_failed(ready):
                    return False, number, url, actual_state, command_output(ready)
            if pr_state == "draft" and actual_state == "ready":
                draft = self.run_command(["gh", "pr", "ready", str(number), "--undo"], cwd=target_repo)
                if command_failed(draft):
                    return False, number, url, actual_state, command_output(draft)
            final_view = self.run_command(view_args, cwd=target_repo)
            if command_failed(final_view):
                return False, number, url, actual_state, command_output(final_view)
            try:
                number, url, actual_state, actual_base, _head = parse_pr_view_state(final_view.stdout)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                return False, number, url, actual_state, f"malformed gh JSON: {exc}"
            if actual_base and actual_base != base_branch:
                return False, number, url, actual_state, f"PR base is {actual_base}, expected {base_branch}"
            if actual_state != pr_state:
                return False, number, url, actual_state, f"PR state is {actual_state}, expected {pr_state}"
            return True, number, url, actual_state, ""

        args = ["gh", "pr", "create", "--base", base_branch, "--title", title]
        if branch:
            args.extend(["--head", branch])
        if body_file is not None:
            args.extend(["--body-file", str(body_file)])
        else:
            args.extend(["--body", body])
        if pr_state == "draft":
            args.append("--draft")
        create = self.run_command(args, cwd=target_repo)
        if command_failed(create):
            return False, 0, "", "not_created", command_output(create)
        url = create.stdout.strip().splitlines()[-1]
        final_view = self.run_command(view_args, cwd=target_repo)
        if not command_failed(final_view):
            try:
                number, url, actual_state, actual_base, _head = parse_pr_view_state(final_view.stdout)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                return False, 0, url, pr_state, f"malformed gh JSON: {exc}"
            if actual_base and actual_base != base_branch:
                return False, number, url, actual_state, f"PR base is {actual_base}, expected {base_branch}"
            if actual_state != pr_state:
                return False, number, url, actual_state, f"PR state is {actual_state}, expected {pr_state}"
            return True, number, url, actual_state, ""
        number = int(url.rstrip("/").split("/")[-1]) if url.rstrip("/").split("/")[-1].isdigit() else 0
        return True, number, url, pr_state, ""

    def runtime_markdown(self, publication: PublicationResult) -> str:
        lines = [
            "## Publication Result",
            "",
            f"- Execution status: `{publication.execution_status}`",
            f"- Branch: `{publication.branch}`",
            f"- Base branch: `{publication.base_branch}`",
            f"- Commit SHA: `{publication.commit_sha}`",
            f"- Branch pushed: `{publication.branch_pushed}`",
            f"- PR URL: `{publication.pr_url}`",
            f"- PR state: `{publication.pr_state}`",
            f"- PR comment posted: `{publication.pr_comment_posted}`",
        ]
        if publication.warnings:
            lines.append(f"- Warnings: {'; '.join(publication.warnings)}")
        if publication.errors:
            lines.append(f"- Errors: {'; '.join(publication.errors)}")
        return "\n".join(lines) + "\n"

    def ensure_runtime_state(
        self,
        publication: PublicationResult,
        change_set: dict[str, Any],
        target_repo: Path,
    ) -> Path:
        if not publication.task_id:
            publication.task_id = str(change_set.get("task_id", ""))
        if not publication.run_id:
            publication.run_id = make_run_id(publication.task_id or "task")
        run_dir = self.runs_dir / publication.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        publication.run_dir = str(run_dir.resolve())
        publication.target_repository = str(target_repo.resolve())
        return run_dir

    def write_runtime_state(self, publication: PublicationResult) -> None:
        if not publication.run_dir:
            return
        run_dir = Path(publication.run_dir)
        write_json(run_dir / "publication.json", publication.as_json())

    def write_final_runtime_artifacts(self, publication: PublicationResult, project_profile: str) -> None:
        run_dir = Path(publication.run_dir)
        final = publication.as_json()
        write_json(run_dir / "publication.json", final)
        write_json(
            run_dir / "summary.json",
            {
                "execution_status": publication.execution_status,
                "project_profile": project_profile,
                "task_id": publication.task_id,
                "target_repository": publication.target_repository,
                "commit_sha": publication.commit_sha,
                "pr_url": publication.pr_url,
                "pr_state": publication.pr_state,
                "warnings": publication.warnings,
                "errors": publication.errors,
                "pr_comment_posted": publication.pr_comment_posted,
                "publication": final,
            },
        )
        (run_dir / "publication.md").write_text(self.runtime_markdown(publication), encoding="utf-8")

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
        verdict["warnings"] = publication.warnings
        verdict["blockers"] = publication.errors
        write_json(self.verdict_path, verdict)

    def update_report(self, publication: PublicationResult) -> None:
        report_path = self.artifacts / "report.md"
        if not report_path.exists():
            return
        text = report_path.read_text(encoding="utf-8").rstrip()
        report_path.write_text(replace_marked_section(text, self.runtime_markdown(publication)), encoding="utf-8")

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
        issue_path.write_text(replace_marked_section(text, self.runtime_markdown(publication)), encoding="utf-8")

    def append_audit_log(self, publication: PublicationResult, project_profile: str) -> None:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "agent": "codex",
            "action": "publish-pr-executor",
            "commit_sha": publication.commit_sha,
            "verdict": publication.execution_status,
            "checks_passed": not publication.errors,
            "project_profile": project_profile,
            "dry_run": publication.dry_run,
        }
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_publication(
        self,
        publication: PublicationResult,
        change_set: dict[str, Any],
        project_profile: str,
    ) -> None:
        self.update_artifacts(publication)
        self.update_report(publication)
        self.update_issue_journal(change_set, project_profile, publication)
        self.append_audit_log(publication, project_profile)

    def post_publication_comment(self, target_repo: Path, publication: PublicationResult) -> None:
        if not publication.pr_url or not publication.run_dir:
            return
        body_path = Path(publication.run_dir) / "publication.md"
        body_path.write_text(self.runtime_markdown(publication), encoding="utf-8")
        comment = self.run_command(
            ["gh", "pr", "comment", publication.pr_url, "--body-file", str(body_path)],
            cwd=target_repo,
        )
        if command_failed(comment):
            publication.warnings.append(f"PR publication comment failed: {command_output(comment)}")
            return
        publication.pr_comment_posted = True

    def finalize(
        self,
        target_repo: Path,
        publication: PublicationResult,
        project_profile: str,
        post_comment: bool = True,
    ) -> None:
        self.write_final_runtime_artifacts(publication, project_profile)
        if post_comment:
            self.post_publication_comment(target_repo, publication)
        self.write_final_runtime_artifacts(publication, project_profile)
        self.update_artifacts(publication)
        self.update_report(publication)
        self.append_audit_log(publication, project_profile)

    def find_resume_state(
        self,
        task_id: str,
        target_repo: Path,
        branch: str,
    ) -> PublicationResult | None:
        if not self.runs_dir.exists():
            return None
        candidates: list[tuple[float, PublicationResult]] = []
        for path in self.runs_dir.glob("*/publication.json"):
            try:
                data = read_json(path)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if data.get("task_id") != task_id:
                continue
            if str(data.get("target_repository", "")) != str(target_repo.resolve()):
                continue
            if data.get("branch") != branch:
                continue
            candidates.append((path.stat().st_mtime, PublicationResult.from_json(data)))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def publish(
        self,
        dry_run: bool = False,
        skip_checks: bool = False,
        repo_override: Path | None = None,
    ) -> PublicationResult:
        if skip_checks and not (dry_run and os.environ.get("AGENT_HARNESS_TEST_MODE") == "1"):
            return self.structured_blocked("--skip-checks is only allowed in AGENT_HARNESS_TEST_MODE=1 dry-run unit tests.", dry_run)
        publication = PublicationResult(dry_run=dry_run)
        try:
            policy, profiles_doc, risk, quality, verdict, project_profile, change_set = self.load_inputs()
            publication.task_id = str(change_set.get("task_id", ""))
            publication.base_branch = str(self.load_publication_payload().get("base_branch", DEFAULT_BASE_BRANCH) or DEFAULT_BASE_BRANCH)
            target_repo = self.resolve_target_repo(change_set, repo_override, publication)
            if publication.errors:
                publication.execution_status = "blocked"
                self.ensure_runtime_state(publication, change_set, target_repo)
                self.finalize(target_repo, publication, str(project_profile.get("project_profile", "")), post_comment=False)
                return publication
            payload = self.load_publication_payload()
            publication.branch = self.publication_branch(target_repo, change_set, payload)
            publication.base_branch = str(payload.get("base_branch", publication.base_branch) or DEFAULT_BASE_BRANCH)
            resume = self.find_resume_state(publication.task_id, target_repo, publication.branch)
            resume_after_irreversible_action = False
            if resume is not None:
                publication = resume
                publication.dry_run = dry_run
                if publication.execution_status == "completed":
                    publication.warnings.append("publication already completed; no-op")
                    self.finalize(target_repo, publication, str(project_profile.get("project_profile", "")), post_comment=False)
                    return publication
                resume_after_irreversible_action = bool(publication.commit_sha or publication.push_completed)
                if resume_after_irreversible_action:
                    publication.errors = []
            self.ensure_runtime_state(publication, change_set, target_repo)
            preflight_verdict = dict(verdict)
            if resume_after_irreversible_action:
                preflight_verdict["decision"] = "publish_pr"
                preflight_verdict["approval_required_before_publish"] = False
                preflight_verdict["blockers"] = []

            preflight = self.preflight(
                target_repo,
                policy,
                profiles_doc,
                risk,
                quality,
                preflight_verdict,
                project_profile,
                change_set,
                skip_checks,
                str(change_set.get("expected_remote", "")),
            )
            for field_name in ("command_results", "warnings", "errors"):
                getattr(publication, field_name).extend(getattr(preflight, field_name))
            publication.pr_state = preflight.pr_state
            if publication.errors:
                publication.execution_status = "blocked"
                self.finalize(target_repo, publication, str(project_profile.get("project_profile", "")), post_comment=False)
                return publication
            publication.execution_status = "preflight_passed"
            self.write_runtime_state(publication)

            title = str(payload.get("title", change_set.get("task_id", "Task changes")))
            body = str(payload.get("body", ""))
            commit_message = str(payload.get("commit_message", title))
            profile_name = str(project_profile.get("project_profile", ""))
            publication.errors.extend(
                forbidden_public_output_blockers(policy, publication.branch, title, body + "\n" + commit_message)
                if profile_name == "flowfox"
                else []
            )
            selected = self.selected_change_set_paths(target_repo, change_set, publication, require_pending_change=not publication.commit_sha)
            if publication.errors:
                publication.execution_status = "blocked"
                self.finalize(target_repo, publication, profile_name, post_comment=False)
                return publication
            if dry_run:
                publication.execution_status = "planned"
                publication.warnings.append("dry-run: no files staged, committed, pushed, or published")
                return publication

            worktree = self.create_worktree(target_repo, change_set, publication)
            if publication.errors:
                publication.execution_status = "failed"
                self.finalize(target_repo, publication, profile_name, post_comment=False)
                return publication
            self.copy_selected_changes(target_repo, worktree, selected, publication)
            if publication.errors:
                publication.execution_status = "failed"
                self.finalize(worktree, publication, profile_name, post_comment=False)
                return publication

            if not publication.commit_sha:
                self.run_selected_security_scan(worktree, profile_name, selected, publication)
                self.run_profile_commands(worktree, profiles_doc, profile_name, publication, "quality_commands")
                self.run_profile_commands(worktree, profiles_doc, profile_name, publication, "security_commands")
                if publication.errors:
                    publication.execution_status = "blocked"
                    self.finalize(worktree, publication, profile_name, post_comment=False)
                    return publication
                self.stage_change_set(worktree, {"include": sorted(selected), "exclude": []}, False, publication)
                if publication.errors:
                    publication.execution_status = "blocked"
                    self.finalize(worktree, publication, profile_name, post_comment=False)
                    return publication
                if self.has_staged_changes(worktree):
                    commit_created, sha, error = self.commit(worktree, commit_message)
                    publication.commit_created = commit_created
                    publication.commit_sha = sha
                    if error:
                        publication.errors.append(f"commit failed: {error}")
                        publication.execution_status = "failed"
                        self.finalize(worktree, publication, profile_name, post_comment=False)
                        return publication
                    publication.execution_status = "committed"
                    self.write_runtime_state(publication)
                else:
                    publication.errors.append("no staged changes and no prior publication commit")
                    publication.execution_status = "blocked"
                    self.finalize(worktree, publication, profile_name, post_comment=False)
                    return publication

            if publication.commit_sha and not publication.push_completed:
                push_error = self.push(worktree, publication.branch)
                if push_error:
                    publication.errors.append(f"push failed: {push_error}")
                    publication.execution_status = "failed"
                    self.write_runtime_state(publication)
                    self.finalize(worktree, publication, profile_name, post_comment=False)
                    return publication
                publication.branch_pushed = True
                publication.push_completed = True
                publication.execution_status = "pushed"
                self.write_runtime_state(publication)

            if publication.push_completed and not publication.pr_created_or_updated:
                body_file = Path(publication.run_dir) / "pr-body.md"
                body_file.write_text(body, encoding="utf-8")
                created, number, url, actual_state, error = self.create_or_update_pr(
                    worktree,
                    title,
                    body,
                    publication.pr_state,
                    base_branch=publication.base_branch,
                    branch=publication.branch,
                    body_file=body_file,
                )
                publication.pr_created_or_updated = created
                publication.pr_number = number
                publication.pr_url = url
                publication.pr_state = actual_state
                if error:
                    publication.errors.append(f"Branch was pushed, but GitHub PR creation failed. {error}")
                    publication.execution_status = "failed"
                    self.write_runtime_state(publication)
                    self.finalize(worktree, publication, profile_name, post_comment=False)
                    return publication
                publication.execution_status = "pr_published"
                self.write_runtime_state(publication)

            publication.execution_status = "completed"
            self.finalize(worktree, publication, profile_name, post_comment=True)
            try:
                if publication.execution_status == "completed" and publication.worktree:
                    remove = self.run_command(["git", "worktree", "remove", "--force", publication.worktree], cwd=target_repo)
                    if command_failed(remove):
                        publication.warnings.append(f"worktree cleanup failed: {command_output(remove)}")
                        self.finalize(worktree, publication, profile_name, post_comment=False)
            except OSError:
                pass
            return publication
        except (FileNotFoundError, json.JSONDecodeError, yaml.YAMLError, subprocess.TimeoutExpired, PermissionError, ValueError) as exc:
            publication.execution_status = "blocked"
            publication.errors.append(self.friendly_error(exc))
            try:
                target_repo = Path(publication.target_repository) if publication.target_repository else self.root
                project_profile_name = ""
                if (self.artifacts / "project_profile.json").exists():
                    project_profile_name = str(read_json(self.artifacts / "project_profile.json").get("project_profile", ""))
                self.ensure_runtime_state(publication, {"task_id": publication.task_id or "unknown-task"}, target_repo)
                self.finalize(target_repo, publication, project_profile_name, post_comment=False)
            except Exception:
                pass
            return publication

    def friendly_error(self, exc: BaseException) -> str:
        if isinstance(exc, json.JSONDecodeError):
            return f"Malformed JSON artifact: {exc.msg}."
        if isinstance(exc, yaml.YAMLError):
            return "Malformed YAML configuration."
        if isinstance(exc, FileNotFoundError):
            return "Required file or command is missing."
        if isinstance(exc, PermissionError):
            return "Permission denied while reading files or running commands."
        if isinstance(exc, subprocess.TimeoutExpired):
            return "Command timed out."
        return str(exc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate and plan without mutations.")
    parser.add_argument("--repo", type=Path, default=None, help="Override target repository path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    publication = Publisher().publish(dry_run=args.dry_run, repo_override=args.repo)
    print(json.dumps(publication.as_json(), indent=2, ensure_ascii=False))
    if publication.errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
