#!/usr/bin/env python3
"""Safe, resumable commit, push, and PR publication executor."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import string
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from repository_registry import RepositoryRecord, find_by_remote
from run_state import RunLayout
from tool_governance import audit_tool_call, authorize_tool_call
from workflow_router import required_gate_roles
from ai_harness.recovery.idempotency import branch_pushed, commit_sha_for_marker, pr_exists
from ai_harness.observability import safe_telemetry_runtime


ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_RESULT_START = "<!-- publication-result:start -->"
PUBLICATION_RESULT_END = "<!-- publication-result:end -->"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
DEFAULT_BASE_BRANCH = "main"
PROTECTED_BRANCH_NAMES = {"main", "master", "trunk"}
PROTECTED_BRANCH_PREFIXES = ("release/", "hotfix/", "prod/", "production/")
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
    input_fingerprint: str = ""
    idempotency_key: str = ""
    reconciled_steps: list[str] = field(default_factory=list)
    pr_comment_posted: bool = False
    command_results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        completed_steps: list[str] = []
        if self.commit_sha:
            completed_steps.append("commit")
        if self.push_completed:
            completed_steps.append("push")
        if self.pr_created_or_updated:
            completed_steps.append("pr")
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
            "input_fingerprint": self.input_fingerprint,
            "idempotency_key": self.idempotency_key,
            "completed_steps": completed_steps,
            "reconciled_steps": self.reconciled_steps,
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


def project_policy(policy: dict[str, Any], project_profile: str) -> dict[str, Any]:
    projects = policy.get("projects", {})
    if not isinstance(projects, dict):
        return {}
    project = projects.get(project_profile, {})
    return project if isinstance(project, dict) else {}


def publication_policy(policy: dict[str, Any], project_profile: str) -> dict[str, Any]:
    project = project_policy(policy, project_profile)
    publication = project.get("publication") if project else None
    if isinstance(publication, dict):
        return publication
    projects = policy.get("projects", {})
    if not isinstance(projects, dict):
        return {}
    for project in projects.values():
        if not isinstance(project, dict):
            continue
        publication = project.get("publication", {})
        if isinstance(publication, dict):
            return publication
    return {}


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
    evidence = verdict.get("visual_evidence", {})
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


def irreversible_resume_blocker(resume: PublicationResult, current_fingerprint: str) -> str:
    if not (resume.commit_sha or resume.push_completed):
        return ""
    if resume.input_fingerprint == current_fingerprint:
        return ""
    return "publication inputs changed after an irreversible side effect; refusing to continue"


def protected_path_blockers(
    change_set: dict[str, Any],
    policy: dict[str, Any],
    project_profile: str,
    protected_patterns: Sequence[str] = (),
) -> list[str]:
    if protected_patterns:
        protected = list(protected_patterns)
    else:
        if project_profile == "agent_workspace":
            return []
        project = project_policy(policy, project_profile)
        protected = project.get("protected_paths", [])
        if not isinstance(protected, list):
            return [f"policy missing {project_profile} protected_paths"]
    return [
        path
        for path in change_set.get("include", [])
        if isinstance(path, str) and matches_any(path, protected)
    ]


def forbidden_public_output_blockers(
    policy: dict[str, Any],
    project_profile: str,
    branch: str,
    title: str,
    body: str,
) -> list[str]:
    project = project_policy(policy, project_profile)
    if not project:
        return []
    phrases = project.get("public_output_forbidden_phrases", [])
    if phrases is None:
        return []
    if not isinstance(phrases, list):
        return ["policy missing public_output_forbidden_phrases"]
    public_text = "\n".join([branch, title, body])
    return [f"public output contains forbidden phrase: {phrase}" for phrase in phrases if phrase in public_text]


def allowed_branch_prefixes(policy: dict[str, Any], project_profile: str) -> list[str]:
    publication = publication_policy(policy, project_profile)
    prefixes = publication.get("allowed_branch_prefixes") if isinstance(publication, dict) else None
    if not isinstance(prefixes, list):
        return []
    return [prefix for prefix in prefixes if isinstance(prefix, str) and prefix]


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
    def __init__(
        self,
        root: Path = ROOT,
        runner: CommandRunner | None = None,
        artifacts_dir: Path | None = None,
        run_id: str = "",
    ) -> None:
        self.root = root.resolve()
        if artifacts_dir is None:
            if not run_id:
                raise ValueError("--run-id or --artifacts-dir is required; root artifacts/ is not runtime state")
            artifacts_dir = self.root / ".agent-runs" / run_id / "artifacts"
        self.artifacts = artifacts_dir.resolve()
        self.policy_path = self.root / ".agent-policy.yaml"
        self.repository_registry_path = self.root / ".agent-repositories.yaml"
        self.project_profiles_path = self.root / ".agent-project-profiles.yaml"
        self.routing_path = self.root / ".agent-routing.yaml"
        self.tool_policy_path = self.root / ".agent-tool-policy.yaml"
        self.publication_path = self.artifacts / "publication.json"
        self.publication_payload_path = self.artifacts / "publication_payload.json"
        self.verdict_path = self.artifacts / "verdict.json"
        self.runs_dir = self.root / ".agent-runs"
        self.worktrees_dir = self.root / ".agent-worktrees"
        self.runner = runner or CommandRunner()
        self.forced_run_id = run_id

    def validate_workflow_gates(self, publication: PublicationResult) -> None:
        workflow_path = self.artifacts.parent / "workflow.json"
        if not workflow_path.exists():
            publication.errors.append("authoritative workflow.json is missing")
            return
        if not self.routing_path.exists():
            publication.errors.append(".agent-routing.yaml is missing")
            return
        try:
            workflow = read_json(workflow_path)
            routing = read_yaml(self.routing_path)
        except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            publication.errors.append(f"cannot validate workflow gates: {exc}")
            return
        required = required_gate_roles(workflow, self.artifacts)
        completed = {
            str(checkpoint.get("role", ""))
            for checkpoint in workflow.get("roles", [])
            if isinstance(checkpoint, dict)
            and isinstance(checkpoint.get("result"), dict)
            and checkpoint["result"].get("status") == "completed"
        }
        missing_roles = [str(role) for role in required if str(role) not in completed]
        if missing_roles:
            publication.errors.append(
                "required workflow gates did not complete: " + ", ".join(missing_roles)
            )
        expected_artifacts = {
            "issue-intake": ("issue.json",),
            "context-compiler": (),
            "planner": ("plan.md", "project_profile.json"),
            "risk-classifier": ("risk.json",),
            "implementation-agent": ("implementation.json",),
            "test-generator": ("test_plan.json", "test_result.json"),
            "quality-runner": ("quality.json",),
            "security-agent": ("security.json",),
            "frontend-qa-agent": ("frontend_qa.json",),
            "architecture-consistency-agent": ("architecture_consistency.json",),
            "semantic-conflict-agent": ("semantic_conflict.json",),
            "reviewer": ("review.json",),
            "orchestrator": ("verdict.json",),
            "publication-prepare": ("change_set.json", "publication_payload.json"),
        }
        missing_artifacts = [
            artifact
            for role in required
            for artifact in expected_artifacts.get(str(role), ())
            if not (self.artifacts / artifact).is_file()
            or (self.artifacts / artifact).stat().st_size == 0
        ]
        if missing_artifacts:
            publication.errors.append(
                "required workflow artifacts are missing: " + ", ".join(missing_artifacts)
            )

    def run_command(self, args: Sequence[str], cwd: Path | None = None) -> CommandResult:
        if self.tool_policy_path.exists() and args:
            tool = "shell"
            action = "project_command"
            domain = ""
            credential_type = ""
            if args[0] == "git" and len(args) > 1:
                tool = "git"
                git_actions = {
                    "rev-parse": "rev_parse",
                    "show-ref": "show_ref",
                    "ls-files": "ls_files",
                }
                action = git_actions.get(args[1], args[1].replace("-", "_"))
                if args[1] == "push" and any(value in {"--force", "-f"} for value in args[2:]):
                    action = "force_push"
            elif args[0] == "gh" and len(args) > 1:
                tool = "github"
                domain = "github.com"
                credential_type = "gh_auth"
                if args[1:3] == ["auth", "status"]:
                    action = "auth_status"
                elif args[1:3] == ["pr", "view"]:
                    action = "read_pr"
                elif args[1:3] == ["pr", "create"]:
                    action = "create_pr"
                elif args[1:3] == ["pr", "comment"]:
                    action = "comment_pr"
                elif args[1:3] == ["pr", "ready"]:
                    action = "mark_ready"
                else:
                    action = "update_pr"
            decision = authorize_tool_call(
                role="publication",
                tool=tool,
                action=action,
                domain=domain,
                credential_type=credential_type,
                timeout_seconds=getattr(self.runner, "timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS),
                policy_path=self.tool_policy_path,
            )
            audit_tool_call(self.artifacts.parent, decision, phase="publication-command")
            if not decision.allowed:
                return CommandResult(126, "", f"tool governance denied {tool}/{action}: {decision.reason}")
        return self.runner.run(args, cwd or self.root)

    def trusted_repository_record(
        self,
        target_repo: Path,
        publication: PublicationResult,
    ) -> RepositoryRecord | None:
        if not self.repository_registry_path.exists():
            return None
        remote = self.run_command(["git", "remote", "get-url", "origin"], cwd=target_repo)
        if command_failed(remote):
            publication.errors.append(f"cannot read origin remote: {command_output(remote)}")
            return None
        try:
            record = find_by_remote(remote.stdout.strip(), self.repository_registry_path)
        except ValueError as exc:
            publication.errors.append(str(exc))
            return None
        if record is None:
            publication.errors.append("target repository remote is not trusted by .agent-repositories.yaml")
        return record

    def policy_with_registry_record(
        self,
        policy: dict[str, Any],
        record: RepositoryRecord | None,
    ) -> dict[str, Any]:
        if record is None:
            return policy
        effective = json.loads(json.dumps(policy))
        publication = (
            effective.setdefault("projects", {})
            .setdefault(record.project_profile, {})
            .setdefault("publication", {})
        )
        publication["allowed_branch_prefixes"] = list(record.allowed_branch_prefixes)
        return effective

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

    def authoritative_task_worktree(
        self,
        target_repo: Path,
        publication: PublicationResult,
    ) -> Path:
        workflow_path = self.artifacts.parent / "workflow.json"
        try:
            workflow = read_json(workflow_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            publication.errors.append(f"cannot resolve authoritative task worktree: {exc}")
            return target_repo
        raw_worktree = workflow.get("worktree")
        if not isinstance(raw_worktree, str) or not raw_worktree:
            publication.errors.append("workflow.json does not record the task worktree")
            return target_repo
        worktree = Path(raw_worktree).resolve()
        if worktree != target_repo.resolve():
            publication.errors.append(
                "publication repository must be the original task worktree from workflow.json"
            )
            return target_repo
        if not worktree.is_dir():
            publication.errors.append("authoritative task worktree does not exist")
            return target_repo
        workflow_branch = workflow.get("branch")
        actual_branch = self.current_branch(worktree)
        if not isinstance(workflow_branch, str) or not workflow_branch:
            publication.errors.append("workflow.json does not record the task branch")
        elif actual_branch != workflow_branch or publication.branch != workflow_branch:
            publication.errors.append(
                f"task worktree branch mismatch: workflow={workflow_branch!r}, "
                f"actual={actual_branch!r}, publication={publication.branch!r}"
            )
        publication.worktree = str(worktree)
        return worktree

    def publication_branch(self, target_repo: Path, change_set: dict[str, Any], payload: dict[str, Any]) -> str:
        explicit = payload.get("branch") or change_set.get("branch")
        if isinstance(explicit, str) and explicit.strip():
            return sanitize_slug(explicit)
        task_id = sanitize_slug(str(change_set.get("task_id", "task")))
        return f"issue/{task_id.removeprefix('issue-')}" if task_id.startswith("issue-") else f"feat/{task_id}"

    def validate_publication_branch(
        self,
        branch: str,
        base_branch: str,
        publication: PublicationResult,
        allowed_prefixes: Sequence[str],
    ) -> None:
        if not branch:
            publication.errors.append("publication branch is missing")
            return
        if not allowed_prefixes:
            publication.errors.append("policy missing publication.allowed_branch_prefixes")
            return
        if branch == base_branch or branch in PROTECTED_BRANCH_NAMES:
            publication.errors.append(f"protected branch {branch!r} blocks publication")
        if branch.startswith(PROTECTED_BRANCH_PREFIXES):
            publication.errors.append(f"protected branch prefix blocks publication: {branch}")
        if not branch.startswith(tuple(allowed_prefixes)):
            allowed = ", ".join(allowed_prefixes)
            publication.errors.append(f"publication branch must start with one of: {allowed}")

    def ensure_base_branch(self, target_repo: Path, base_branch: str, publication: PublicationResult) -> None:
        if not base_branch or not safe_relative_path(base_branch):
            publication.errors.append(f"invalid base branch: {base_branch!r}")
            return
        fetch = self.run_command(["git", "fetch", "--prune", "origin"], cwd=target_repo)
        append_command_result(publication, "git fetch --prune origin", fetch, "required")
        if command_failed(fetch):
            publication.errors.append(f"cannot fetch origin before publication: {command_output(fetch)}")
            return
        verify = self.run_command(["git", "rev-parse", "--verify", f"origin/{base_branch}"], cwd=target_repo)
        append_command_result(publication, f"git rev-parse --verify origin/{base_branch}", verify, "required")
        if command_failed(verify):
            publication.errors.append(f"base branch origin/{base_branch} does not exist")

    def current_head(self, target_repo: Path) -> str:
        result = self.run_command(["git", "rev-parse", "HEAD"], cwd=target_repo)
        return result.stdout.strip() if not command_failed(result) else ""

    def input_fingerprint(
        self,
        target_repo: Path,
        change_set: dict[str, Any],
        payload: dict[str, Any],
        policy: dict[str, Any],
        profiles_doc: dict[str, Any],
        project_profile: str,
        branch: str,
        base_branch: str,
    ) -> str:
        include = change_set.get("include", [])
        exclude = [path for path in change_set.get("exclude", []) if isinstance(path, str)]
        selected = [
            path
            for path in include
            if isinstance(path, str) and safe_relative_path(path) and not matches_any(path, exclude)
        ]
        file_hashes: dict[str, str] = {}
        for relative in sorted(selected):
            path = target_repo / relative
            if path.exists() and path.is_file():
                file_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.exists() and path.is_dir():
                file_hashes[relative] = "directory"
            else:
                file_hashes[relative] = "missing"
        profile_doc = profiles_doc.get("profiles", {}).get(project_profile, {})
        data = {
            "selected_paths": sorted(selected),
            "file_hashes": file_hashes,
            "base_branch": base_branch,
            "branch": branch,
            "publication_payload": payload,
            "policy_version": policy.get("version"),
            "profile_version": profiles_doc.get("version"),
            "profile": project_profile,
            "profile_doc": profile_doc,
        }
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

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

    def changed_paths(self, target_repo: Path) -> set[str]:
        diff = self.run_command(["git", "diff", "--name-only", "HEAD"], cwd=target_repo)
        changed = {line.strip() for line in diff.stdout.splitlines() if line.strip()} if not command_failed(diff) else set()
        untracked = self.run_command(["git", "ls-files", "--others", "--exclude-standard"], cwd=target_repo)
        if not command_failed(untracked):
            changed.update(line.strip() for line in untracked.stdout.splitlines() if line.strip())
        return changed

    def validate_change_set_completeness(
        self,
        target_repo: Path,
        change_set: dict[str, Any],
        risk: dict[str, Any],
        publication: PublicationResult,
    ) -> None:
        include = {path for path in change_set.get("include", []) if isinstance(path, str)}
        exclude = [path for path in change_set.get("exclude", []) if isinstance(path, str)]
        changed = {path for path in self.changed_paths(target_repo) if not matches_any(path, exclude)}
        missing_from_change_set = sorted(changed - include)
        if missing_from_change_set:
            publication.errors.append(
                "changed files missing from change_set.include: " + ", ".join(missing_from_change_set)
            )
        risk_areas = {path for path in risk.get("changed_areas", []) if isinstance(path, str)}
        missing_from_risk = sorted(include - risk_areas) if risk_areas else []
        if missing_from_risk:
            publication.errors.append(
                "change_set.include files missing from risk.changed_areas: " + ", ".join(missing_from_risk)
            )

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
        branch: str = "",
        base_branch: str = DEFAULT_BASE_BRANCH,
        protected_patterns: Sequence[str] = (),
    ) -> PublicationResult:
        result = PublicationResult(target_repository=str(target_repo))
        profile = str(project_profile.get("project_profile", ""))
        result.task_id = str(change_set.get("task_id", ""))
        result.pr_state = determine_pr_state(quality, verdict)
        self.validate_workflow_gates(result)
        self.validate_orchestrator_decision(risk, verdict, result)
        if change_set.get("project_profile") != profile:
            result.errors.append("change_set project_profile does not match project_profile artifact")
        for protected_path in protected_path_blockers(change_set, policy, profile, protected_patterns):
            result.errors.append(f"protected path in change set: {protected_path}")
        if branch:
            self.validate_publication_branch(branch, base_branch, result, allowed_branch_prefixes(policy, profile))

        if not skip_checks:
            validation_command = [
                "python3",
                "scripts/validate_artifacts.py",
                "--artifacts-dir",
                str(self.artifacts),
                "--phase",
                "pre-publication",
            ]
            validation = self.run_command(validation_command, cwd=self.root)
            append_command_result(result, " ".join(validation_command), validation, "required")
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
        if branch:
            self.ensure_base_branch(target_repo, base_branch, result)

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
        for status in ("required",):
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
        return f"origin/{base_branch}"

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
        branch_check = self.run_command(
            ["git", "show-ref", "--verify", f"refs/heads/{publication.branch}"],
            cwd=target_repo,
        )
        if command_failed(branch_check):
            base_ref = self.source_ref(target_repo, publication.base_branch)
            args = ["git", "worktree", "add", "-b", publication.branch, str(worktree), base_ref]
        else:
            args = ["git", "worktree", "add", str(worktree), publication.branch]
        result = self.run_command(args, cwd=target_repo)
        if command_failed(result):
            publication.errors.append(f"git worktree add failed: {command_output(result)}")
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

    def commit(self, target_repo: Path, message: str, idempotency_key: str = "") -> tuple[bool, str, str]:
        commit_message = message
        if idempotency_key:
            commit_message += f"\n\nTask-Idempotency-Key: {idempotency_key}"
        commit_result = self.run_command(["git", "commit", "-m", commit_message], cwd=target_repo)
        if command_failed(commit_result):
            return False, "", command_output(commit_result)
        sha = self.run_command(["git", "rev-parse", "HEAD"], cwd=target_repo)
        if command_failed(sha):
            return True, "", command_output(sha)
        return True, sha.stdout.strip(), ""

    def reconcile_side_effects(
        self,
        target_repo: Path,
        publication: PublicationResult,
    ) -> None:
        """Recover durable publication state after a process dies between effect and checkpoint."""
        runner = lambda args, cwd: self.run_command(args, cwd=cwd)
        changed = False
        prevented: list[str] = []
        if not publication.commit_sha and publication.idempotency_key:
            sha = commit_sha_for_marker(target_repo, publication.idempotency_key, runner)
            if sha:
                publication.commit_created = True
                publication.commit_sha = sha
                publication.execution_status = "committed"
                if "commit" not in publication.reconciled_steps:
                    publication.reconciled_steps.append("commit")
                prevented.append("commit")
                changed = True
        if publication.commit_sha and not publication.push_completed:
            if branch_pushed(target_repo, publication.branch, runner):
                publication.branch_pushed = True
                publication.push_completed = True
                publication.execution_status = "pushed"
                if "push" not in publication.reconciled_steps:
                    publication.reconciled_steps.append("push")
                prevented.append("push")
                changed = True
        if publication.push_completed and not publication.pr_created_or_updated:
            existing = pr_exists(
                target_repo,
                publication.branch,
                runner,
                markers=(publication.run_id, publication.idempotency_key),
            )
            if existing is not None:
                publication.pr_created_or_updated = True
                publication.pr_number, publication.pr_url = existing
                publication.execution_status = "pr_published"
                if "pr" not in publication.reconciled_steps:
                    publication.reconciled_steps.append("pr")
                prevented.append("pr")
                changed = True
        self.record_idempotency_check(publication, prevented)
        if changed:
            self.write_runtime_state(publication)

    def record_idempotency_check(self, publication: PublicationResult, prevented: list[str]) -> None:
        """Emit the required publication signal without letting telemetry affect publication."""

        try:
            telemetry = safe_telemetry_runtime(
                run_dir=self.artifacts.parent,
                service_name="ai-harness-publication",
            )
        except Exception:
            return
        try:
            with telemetry.span(
                "ai_harness.publication.idempotency_check",
                {"publication.run_id": publication.run_id, "publication.branch": publication.branch},
            ) as span:
                span.set_attribute("publication.prevented_steps", prevented)
            for step in prevented:
                telemetry.duplicate_side_effects_prevented_total.add(1, {"side_effect": step})
        except Exception:
            pass
        finally:
            try:
                telemetry.shutdown()
            except Exception:
                pass

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
            publication.run_id = self.forced_run_id or self.artifacts.parent.name
        layout = RunLayout.create(self.runs_dir, publication.run_id)
        if layout.artifacts.resolve() != self.artifacts:
            raise ValueError("publication artifacts must belong to the authoritative run directory")
        publication.run_dir = str(layout.root)
        publication.idempotency_key = f"{publication.run_id}:publication"
        publication.target_repository = str(target_repo.resolve())
        return layout.root

    def write_runtime_state(self, publication: PublicationResult) -> None:
        write_json(self.publication_path, publication.as_json())

    def write_final_runtime_artifacts(self, publication: PublicationResult, project_profile: str) -> None:
        self.write_runtime_state(publication)

    def update_artifacts(self, publication: PublicationResult) -> None:
        write_json(self.publication_path, publication.as_json())

    def update_report(self, publication: PublicationResult) -> None:
        return

    def update_issue_journal(
        self,
        change_set: dict[str, Any],
        project_profile: str,
        publication: PublicationResult,
    ) -> None:
        task_id = str(change_set.get("task_id", ""))
        match = re.search(r"issue-(\d+)", task_id)
        if match is None:
            return
        project_id = str(change_set.get("project") or change_set.get("repository_id") or "").strip()
        if not project_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", project_id):
            return
        issue_path = self.root / "docs" / "projects" / project_id / "issues" / f"issue-{match.group(1)}.md"
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
        audit_log_path = Path(publication.run_dir) / "audit-log.jsonl"
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_log_path.open("a", encoding="utf-8") as handle:
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
        comment = self.run_command(
            ["gh", "pr", "comment", publication.pr_url, "--body", self.runtime_markdown(publication)],
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

    def cleanup_worktree(self, target_repo: Path, publication: PublicationResult) -> None:
        if not publication.worktree:
            return
        worktree = Path(publication.worktree)
        workflow_path = self.artifacts.parent / "workflow.json"
        if workflow_path.exists():
            try:
                workflow = read_json(workflow_path)
            except (OSError, ValueError, json.JSONDecodeError):
                workflow = {}
            if str(workflow.get("worktree", "")) and Path(str(workflow["worktree"])).resolve() == worktree.resolve():
                return
        if not worktree.exists():
            return
        remove = self.run_command(["git", "worktree", "remove", "--force", publication.worktree], cwd=target_repo)
        if command_failed(remove):
            publication.warnings.append(f"worktree cleanup failed: {command_output(remove)}")

    def find_resume_state(
        self,
        task_id: str,
        target_repo: Path,
        branch: str,
    ) -> PublicationResult | None:
        if not self.runs_dir.exists():
            return None
        candidates: list[tuple[float, PublicationResult]] = []
        for path in self.runs_dir.glob("*/artifacts/publication.json"):
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
        if not self.tool_policy_path.exists():
            return self.structured_blocked(".agent-tool-policy.yaml is required for publication.", dry_run)
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
            registry_record = self.trusted_repository_record(target_repo, publication)
            if publication.errors:
                publication.execution_status = "blocked"
                self.ensure_runtime_state(publication, change_set, target_repo)
                self.finalize(target_repo, publication, str(project_profile.get("project_profile", "")), post_comment=False)
                return publication
            if registry_record is not None:
                registry_profile = registry_record.project_profile
                artifact_profile = str(project_profile.get("project_profile", ""))
                if registry_profile != artifact_profile:
                    publication.errors.append("project profile does not match trusted repository registry")
                    publication.execution_status = "blocked"
                    self.ensure_runtime_state(publication, change_set, target_repo)
                    self.finalize(target_repo, publication, artifact_profile, post_comment=False)
                    return publication
                policy = self.policy_with_registry_record(policy, registry_record)
            payload = self.load_publication_payload()
            publication.branch = self.publication_branch(target_repo, change_set, payload)
            publication.base_branch = (
                registry_record.base_branch
                if registry_record is not None
                else str(payload.get("base_branch", publication.base_branch) or DEFAULT_BASE_BRANCH)
            )
            profile_name = str(project_profile.get("project_profile", ""))
            publication.input_fingerprint = self.input_fingerprint(
                target_repo,
                change_set,
                payload,
                policy,
                profiles_doc,
                profile_name,
                publication.branch,
                publication.base_branch,
            )
            resume = self.find_resume_state(publication.task_id, target_repo, publication.branch)
            previous_precommit_blocked = False
            previous_precommit_errors: list[str] = []
            if resume is not None:
                same_fingerprint = resume.input_fingerprint == publication.input_fingerprint
                if resume.execution_status == "completed" and same_fingerprint:
                    publication = resume
                    publication.dry_run = dry_run
                    publication.warnings.append("publication already completed; no-op")
                    completed_steps = [
                        step
                        for step, completed in (
                            ("commit", bool(publication.commit_sha)),
                            ("push", publication.push_completed),
                            ("pr", publication.pr_created_or_updated),
                        )
                        if completed
                    ]
                    self.record_idempotency_check(publication, completed_steps)
                    self.finalize(target_repo, publication, str(project_profile.get("project_profile", "")), post_comment=False)
                    return publication
                if resume.execution_status != "completed" and (resume.commit_sha or resume.push_completed):
                    resume_blocker = irreversible_resume_blocker(resume, publication.input_fingerprint)
                    if resume_blocker:
                        publication = resume
                        publication.dry_run = dry_run
                        publication.execution_status = "blocked"
                        publication.errors = [resume_blocker]
                        self.ensure_runtime_state(publication, change_set, target_repo)
                        self.finalize(
                            target_repo,
                            publication,
                            str(project_profile.get("project_profile", "")),
                            post_comment=False,
                        )
                        return publication
                    fingerprint = publication.input_fingerprint
                    publication = resume
                    publication.dry_run = dry_run
                    publication.input_fingerprint = fingerprint
                    publication.errors = []
                elif resume.execution_status in {"blocked", "failed"}:
                    previous_precommit_blocked = True
                    previous_precommit_errors = list(resume.errors)
            self.ensure_runtime_state(publication, change_set, target_repo)
            preflight_verdict = dict(verdict)
            if (
                previous_precommit_blocked
                and previous_precommit_errors
                and preflight_verdict.get("blockers") == previous_precommit_errors
            ):
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
                publication.branch,
                publication.base_branch,
                registry_record.protected_paths if registry_record is not None else (),
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
            publication.errors.extend(
                forbidden_public_output_blockers(
                    policy,
                    profile_name,
                    publication.branch,
                    title,
                    body + "\n" + commit_message,
                )
            )
            worktree = self.authoritative_task_worktree(target_repo, publication)
            if publication.errors:
                publication.execution_status = "blocked"
                self.finalize(target_repo, publication, profile_name, post_comment=False)
                return publication
            self.reconcile_side_effects(worktree, publication)

            selected = self.selected_change_set_paths(
                target_repo,
                change_set,
                publication,
                require_pending_change=not publication.commit_sha,
            )
            self.validate_change_set_completeness(target_repo, change_set, risk, publication)
            if publication.errors:
                publication.execution_status = "blocked"
                self.finalize(target_repo, publication, profile_name, post_comment=False)
                return publication

            if not publication.commit_sha:
                self.run_selected_security_scan(worktree, profile_name, selected, publication)
                self.run_profile_commands(worktree, profiles_doc, profile_name, publication, "quality_commands")
                self.run_profile_commands(worktree, profiles_doc, profile_name, publication, "security_commands")
                if publication.errors:
                    publication.execution_status = "blocked"
                    if not dry_run:
                        self.finalize(worktree, publication, profile_name, post_comment=False)
                    else:
                        self.cleanup_worktree(target_repo, publication)
                    return publication
                if dry_run:
                    publication.execution_status = "planned"
                    publication.warnings.append("dry-run: selected files scanned and checks completed; no files staged, committed, pushed, or published")
                    self.cleanup_worktree(target_repo, publication)
                    return publication
                self.stage_change_set(worktree, {"include": sorted(selected), "exclude": []}, False, publication)
                if publication.errors:
                    publication.execution_status = "blocked"
                    self.finalize(worktree, publication, profile_name, post_comment=False)
                    return publication
                if self.has_staged_changes(worktree):
                    commit_created, sha, error = self.commit(
                        worktree,
                        commit_message,
                        publication.idempotency_key,
                    )
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

            if dry_run:
                publication.execution_status = "planned"
                publication.warnings.append("dry-run: resume state inspected; no push or PR action was performed")
                self.cleanup_worktree(target_repo, publication)
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
                    self.cleanup_worktree(target_repo, publication)
                    if publication.warnings and publication.warnings[-1].startswith("worktree cleanup failed"):
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
    parser.add_argument("--artifacts-dir", type=Path, default=None, help="Read and write task artifacts from this directory.")
    parser.add_argument("--run-id", default="", help="Use an existing or caller-provided run id.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    publication = Publisher(artifacts_dir=args.artifacts_dir, run_id=args.run_id).publish(
        dry_run=args.dry_run,
        repo_override=args.repo,
    )
    print(json.dumps(publication.as_json(), indent=2, ensure_ascii=False))
    if publication.errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
