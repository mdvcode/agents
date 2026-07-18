#!/usr/bin/env python3
"""Run the agent-role workflow with strict adapter and run-scoped artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from adapters.codex_adapter import CodexAdapter, contract_section, load_json, validate_contract
from check_codex_runtime import check_codex_runtime
from context_compiler import create_context_manifest, role_capability, role_contract
from repository_registry import RepositoryRecord, find_by_remote
from run_state import (
    RunLayout,
    file_contents_snapshot,
    file_snapshot,
    find_completed_run,
    ownership_errors,
    record_failure,
    restore_foreign_artifacts,
    task_fingerprint,
    write_metrics,
)
from tool_preflight import role_tool_preflight
from validate_artifacts import validate_required as validate_artifact_required
from workflow_router import decide_next_role, load_yaml
from worktree_manager import create_worktree, slug


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / ".agents" / "prompts"
RUNS = ROOT / ".agent-runs"
SCHEMAS = ROOT / "schemas"
ROLE_CHAIN = [
    "issue-intake",
    "context-compiler",
    "planner",
    "risk-classifier",
    "implementation-agent",
    "test-generator",
    "quality-runner",
    "security-agent",
    "frontend-qa-agent",
    "architecture-consistency-agent",
    "semantic-conflict-agent",
    "reviewer",
    "ci-repair-agent",
    "orchestrator",
    "eval-runner",
    "report-agent",
    "publication-prepare",
    "publication",
]
INTERNAL_ROLES = {"issue-intake", "context-compiler", "publication-prepare"}
ADAPTER_ROLES = set(ROLE_CHAIN) - INTERNAL_ROLES - {"publication"}
PROMPT_FILES = {
    "context-compiler": "context-compiler.md",
    "planner": "planner.md",
    "risk-classifier": "risk-classifier.md",
    "implementation-agent": "implementation-agent.md",
    "test-generator": "test-generator.md",
    "quality-runner": "quality-runner.md",
    "security-agent": "security-agent.md",
    "frontend-qa-agent": "frontend-qa-agent.md",
    "architecture-consistency-agent": "architecture-consistency-agent.md",
    "semantic-conflict-agent": "semantic-conflict-agent.md",
    "reviewer": "reviewer.md",
    "ci-repair-agent": "ci-repair-agent.md",
    "orchestrator": "orchestrator.md",
    "eval-runner": "eval-runner.md",
    "report-agent": "report-agent.md",
}
DEFAULT_ALLOWED_TOOLS = ["filesystem_read", "repository_search"]
KNOWN_PROJECT_PROFILES = {"agent_workspace", "django", "nextjs_web"}
DEFAULT_CODEX_ADAPTER_COMMAND = "python3 scripts/adapters/codex_cli_executor.py"


def make_run_id(workflow: str) -> str:
    return datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S.%fZ-{workflow}")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_trace(layout: RunLayout, event: dict[str, Any]) -> None:
    with (layout.raw_events / "workflow.jsonl").open("a", encoding="utf-8") as trace:
        trace.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False) + "\n")


def role_prompt(role: str) -> str:
    prompt_file = PROMPT_FILES.get(role)
    if not prompt_file:
        return ""
    path = PROMPTS / prompt_file
    return path.read_text(encoding="utf-8") if path.exists() else ""


def completed_result(summary: str, next_action: str = "continue") -> dict[str, Any]:
    return {
        "status": "completed",
        "next_action": next_action,
        "summary": summary,
        "artifacts_created": [],
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def blocked_result(summary: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "next_action": "blocked",
        "summary": summary,
        "artifacts_created": [],
        "blockers": blockers,
        "warnings": [],
        "tokens_used": 0,
        "duration_ms": 0,
    }


def artifact_result(summary: str, artifacts_created: list[str], next_action: str = "continue") -> dict[str, Any]:
    return {
        "status": "completed",
        "next_action": next_action,
        "summary": summary,
        "artifacts_created": artifacts_created,
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def awaiting_approval_result(summary: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "status": "awaiting_approval",
        "next_action": "awaiting_approval",
        "summary": summary,
        "artifacts_created": [],
        "blockers": blockers,
        "warnings": [],
        "tokens_used": 0,
        "duration_ms": 0,
    }


def validate_role_result(result: dict[str, Any], role: str) -> list[str]:
    return validate_contract(result, load_json(SCHEMAS / "role_result.schema.json"), f"{role} role_result")


def validate_manifest(path: Path, role: str) -> list[str]:
    data = load_json(path)
    return validate_contract(data, load_json(SCHEMAS / "context_manifest.schema.json"), f"{role} context_manifest")


def project_profile_for(project: str) -> str:
    return project if project in KNOWN_PROJECT_PROFILES else "agent_workspace"


def role_tools(role: str) -> list[str]:
    tools = role_capability(role).get("tools", DEFAULT_ALLOWED_TOOLS)
    return list(tools) if isinstance(tools, list) else list(DEFAULT_ALLOWED_TOOLS)


def role_filesystem_access(role: str) -> str:
    return str(role_capability(role).get("filesystem", "read_only"))


def safe_artifact_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def ensure_project_profile_artifact(artifacts_dir: Path, project_profile: str) -> None:
    path = artifacts_dir / "project_profile.json"
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            data = {}
    profiles = load_yaml(ROOT / ".agent-project-profiles.yaml").get("profiles", {})
    profile = profiles.get(project_profile, {}) if isinstance(profiles, dict) else {}
    quality = profile.get("quality_commands", {}) if isinstance(profile, dict) else {}
    security = profile.get("security_commands", {}) if isinstance(profile, dict) else {}
    required_quality = quality.get("required", []) if isinstance(quality, dict) else []
    required_security = security.get("required", []) if isinstance(security, dict) else []
    frontend = profile.get("frontend_evidence", {}) if isinstance(profile, dict) else {}
    quality_selected = list(data.get("quality_commands_selected", []))
    security_selected = list(data.get("security_commands_selected", []))
    for command in required_quality:
        if command not in quality_selected:
            quality_selected.append(command)
    for command in required_security:
        if command not in security_selected:
            security_selected.append(command)
    data.update(
        {
            "project_profile": project_profile,
            "confidence": data.get("confidence", "high"),
            "reasons": data.get("reasons", ["Selected by the workflow project profile resolver."]),
            "matched_markers": data.get("matched_markers", []),
            "quality_commands_selected": quality_selected,
            "security_commands_selected": security_selected,
            "frontend_evidence_required": bool(
                data.get("frontend_evidence_required", False)
                or (isinstance(frontend, dict) and frontend.get("required") is True)
            ),
            "warnings": data.get("warnings", []),
        }
    )
    write_json(
        path,
        data,
    )


def ensure_deterministic_role_artifacts(role: str, artifacts_dir: Path, project_profile: str) -> list[str]:
    if role != "planner":
        return []
    ensure_project_profile_artifact(artifacts_dir, project_profile)
    return ["project_profile.json"]


def git_snapshot(repo: Path) -> str:
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return ""
    digest = hashlib.sha256()
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary"],
        cwd=repo,
        text=False,
        capture_output=True,
        check=False,
    )
    if diff.returncode == 0:
        digest.update(diff.stdout)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if untracked.returncode == 0:
        for relative in sorted(line.strip() for line in untracked.stdout.splitlines() if line.strip()):
            digest.update(relative.encode("utf-8"))
            path = repo / relative
            if path.is_file():
                digest.update(path.read_bytes())
    return digest.hexdigest()


def changed_paths(repo: Path) -> list[str]:
    paths: set[str] = set()
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if diff.returncode == 0:
        paths.update(line.strip() for line in diff.stdout.splitlines() if line.strip())
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if untracked.returncode == 0:
        paths.update(line.strip() for line in untracked.stdout.splitlines() if line.strip())
    return sorted(paths)


def validate_role_artifacts(
    *,
    role: str,
    result: dict[str, Any],
    artifacts_dir: Path,
    worktree: Path,
    source_repository: Path,
    source_snapshot_before: str,
    create_task_worktree: bool,
) -> list[str]:
    errors: list[str] = []
    contract = role_contract(role)
    expected = {str(item) for item in contract.get("expected_artifacts", []) if isinstance(item, str)}
    for artifact in result.get("artifacts_created", []):
        if not safe_artifact_name(artifact):
            errors.append(f"{role}: artifacts_created contains unsafe path {artifact!r}")
            continue
        if role in ADAPTER_ROLES and str(artifact) not in expected:
            errors.append(f"{role} cannot claim artifact it does not own: {artifact}")
    for expected in contract.get("expected_artifacts", []):
        if not safe_artifact_name(expected):
            errors.append(f"{role}: expected artifact uses unsafe path {expected!r}")
            continue
        expected_path = artifacts_dir / expected
        if not expected_path.exists():
            errors.append(f"{role} must create run-scoped {expected}")
            continue
        if expected_path.is_file() and expected_path.stat().st_size == 0:
            errors.append(f"{role} must create non-empty run-scoped {expected}")

    artifact_schemas = contract.get("artifact_schemas", {})
    if isinstance(artifact_schemas, dict):
        for artifact, schema_path in artifact_schemas.items():
            if not isinstance(artifact, str) or not isinstance(schema_path, str):
                errors.append(f"{role}: artifact_schemas entries must be string paths")
                continue
            if not safe_artifact_name(artifact):
                errors.append(f"{role}: artifact schema uses unsafe artifact path {artifact!r}")
                continue
            artifact_path = artifacts_dir / artifact
            if not artifact_path.exists():
                continue
            try:
                data = load_json(artifact_path)
                schema_file = Path(schema_path)
                schema = load_json(schema_file if schema_file.is_absolute() else ROOT / schema_file)
                errors.extend(validate_artifact_required(data, contract_section(schema, "artifact"), artifact))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{artifact} is invalid: {exc}")
    if (
        role_filesystem_access(role) == "task_worktree_write"
        and create_task_worktree
        and worktree.resolve() != source_repository.resolve()
    ):
        source_snapshot_after = git_snapshot(source_repository)
        if source_snapshot_after != source_snapshot_before:
            errors.append(f"{role} changed the source repository instead of only the task worktree")
    return errors


def next_role_name(current_role: str, result: dict[str, Any]) -> str:
    """Deprecated compatibility helper; authoritative routing lives in workflow_router."""
    index = ROLE_CHAIN.index(current_role)
    return ROLE_CHAIN[index + 1] if index + 1 < len(ROLE_CHAIN) else ""


def workflow_budgets(workflow: str) -> dict[str, int]:
    document = load_yaml(ROOT / ".agent-workflows.yaml")
    configured = document.get("workflows", {}).get(workflow, {}).get("budgets", {})
    defaults = {
        "max_roles": 40,
        "max_repair_iterations": 3,
        "max_duration_seconds": 7200,
        "max_tokens": 300000,
    }
    if isinstance(configured, dict):
        for key in defaults:
            if isinstance(configured.get(key), (int, float)):
                defaults[key] = int(configured[key])
    return defaults


def initial_loops() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "iterations": 0,
            "max_iterations": 3,
            "last_failure_fingerprint": "",
            "last_diff_fingerprint": "",
            "progress_detected": False,
        }
        for name in ("quality_repair", "review_repair", "ci_repair")
    }


def git_remote(repo: Path) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def git_ref_sha(repo: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def resolve_registry_record(repository: Path, project: str) -> tuple[RepositoryRecord | None, list[str]]:
    remote = git_remote(repository)
    if not remote and project:
        return None, ["repository has no origin remote to verify against registry"]
    record = find_by_remote(remote) if remote else None
    if project and record is None:
        return None, ["repository remote is not trusted by .agent-repositories.yaml"]
    if project and record is not None and record.repository_id != project:
        return record, [f"repository registry record is {record.repository_id!r}, expected {project!r}"]
    return record, []


def prepare_worktree(
    repository: Path,
    task_id: str,
    project: str,
    run_id: str,
    branch: str,
    base_branch: str,
    create_task_worktree: bool,
) -> tuple[Path, str, str, list[str]]:
    record, errors = resolve_registry_record(repository, project)
    if errors:
        return repository, "", "", errors
    effective_base = record.base_branch if record is not None else base_branch
    effective_branch = branch or f"issue/{slug(task_id)}"
    if not create_task_worktree:
        return repository, effective_branch, effective_base, []
    result = create_worktree(
        repository,
        task_id,
        effective_branch,
        effective_base,
        run_id,
        runs_dir=RUNS,
        worktrees_dir=RUNS.parent / ".agent-worktrees",
    )
    if result.get("execution_status") != "completed":
        return repository, effective_branch, effective_base, [str(item) for item in result.get("errors", [])]
    return Path(str(result["worktree"])), effective_branch, effective_base, []


def build_role_request(
    *,
    run_id: str,
    role: str,
    goal: str,
    repository: Path,
    artifacts_dir: Path,
    context_manifest: Path,
    token_budget: int,
    timeout_seconds: int,
    project_profile: str,
) -> dict[str, Any]:
    contract = role_contract(role)
    return {
        "run_id": run_id,
        "role": role,
        "goal": goal,
        "repository": str(repository.resolve()),
        "artifacts_dir": str(artifacts_dir.resolve()),
        "context_manifest": str(context_manifest.resolve()),
        "prompt_path": str(contract.get("prompt_path", "")),
        "output_contract": str(contract.get("output_contract", "schemas/role_result.schema.json")),
        "project_profile": project_profile,
        "expected_artifacts": list(contract.get("expected_artifacts", [])),
        "allowed_tools": role_tools(role),
        "filesystem_access": role_filesystem_access(role),
        "token_budget": token_budget,
        "timeout_seconds": timeout_seconds,
    }


def effective_adapter_command(workflow: str, adapter_command: str) -> str:
    if adapter_command:
        return adapter_command
    configured = os.environ.get("AGENT_CODEX_COMMAND", "") or os.environ.get("AGENT_LLM_COMMAND", "")
    if configured:
        return configured
    if workflow == "full_agent_workflow":
        return DEFAULT_CODEX_ADAPTER_COMMAND
    return ""


def uses_production_codex_executor(command: str) -> bool:
    return "codex_cli_executor.py" in command


def frontend_qa_unavailable_result(artifacts_dir: Path, warnings: list[str]) -> dict[str, Any]:
    write_json(
        artifacts_dir / "frontend_qa.json",
        {
            "evidence_required": True,
            "evidence_collected": False,
            "screenshots": [],
            "console_errors": [],
            "network_errors": [],
            "blockers": warnings,
            "local_url": "",
            "dev_server": {},
            "next_action": "continue",
        },
    )
    result = completed_result("Frontend QA evidence is unavailable in this runtime.", "continue")
    result["artifacts_created"] = ["frontend_qa.json"]
    result["warnings"] = warnings
    return result


def preflight_role_execution(
    *,
    role: str,
    project_profile: str,
    artifacts_dir: Path,
    dry_run: bool,
) -> dict[str, Any] | None:
    outcome = role_tool_preflight(
        role=role,
        allowed_tools=role_tools(role),
        project_profile=project_profile,
        dry_run=dry_run,
    )
    if outcome["status"] == "blocked":
        return blocked_result("Role tool preflight failed.", list(outcome["blockers"]))
    if outcome["status"] == "unavailable" and role == "frontend-qa-agent":
        return frontend_qa_unavailable_result(artifacts_dir, list(outcome["warnings"]))
    return None


def run_publication(
    *,
    run_id: str,
    repository: Path,
    artifacts_dir: Path,
    dry_run: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        "python3",
        "scripts/publish_pr.py",
        "--artifacts-dir",
        str(artifacts_dir.resolve()),
        "--run-id",
        run_id,
        "--repo",
        str(repository.resolve()),
    ]
    if dry_run:
        command.append("--dry-run")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        return blocked_result(
            "Publication executor blocked or failed.",
            [(completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"],
        )
    return {
        "status": "completed",
        "next_action": "completed",
        "summary": "Publication executor completed.",
        "artifacts_created": ["publication.json"],
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def high_risk_requested_approval(artifacts_dir: Path) -> bool:
    risk_path = artifacts_dir / "risk.json"
    if not risk_path.exists():
        return False
    try:
        risk = load_json(risk_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    return risk.get("risk_class") == "high"


def run_issue_intake(
    *,
    run_id: str,
    task_id: str,
    goal: str,
    project: str,
    repository: Path,
    worktree: Path,
    branch: str,
    base_branch: str,
    artifacts_dir: Path,
) -> dict[str, Any]:
    intake = {
        "run_id": run_id,
        "task_id": task_id,
        "goal": goal,
        "project": project,
        "repository": str(repository.resolve()),
        "worktree": str(worktree.resolve()),
        "branch": branch,
        "base_branch": base_branch,
    }
    write_json(artifacts_dir / "issue.json", intake)
    return {
        "status": "completed",
        "next_action": "context-compiler",
        "summary": "Issue intake recorded.",
        "artifacts_created": ["issue.json"],
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def run_context_compiler(
    *,
    run_id: str,
    goal: str,
    project: str,
    worktree: Path,
    artifacts_dir: Path,
    context_dir: Path,
    project_profile: str,
    token_budget: int,
) -> dict[str, Any]:
    contract = role_contract("planner")
    manifest_path = create_context_manifest(
        run_id=run_id,
        role="planner",
        goal=goal,
        repository=worktree,
        artifacts_dir=artifacts_dir,
        context_dir=context_dir,
        project=project,
        project_profile=project_profile,
        token_budget=token_budget,
        allowed_tools=role_tools("planner"),
        previous_roles=["issue-intake", "context-compiler"],
        filesystem_access=role_filesystem_access("planner"),
        prompt_path=str(contract.get("prompt_path", "")),
        output_contract=str(contract.get("output_contract", "")),
        expected_artifacts=list(contract.get("expected_artifacts", [])),
    )
    manifest_errors = validate_manifest(manifest_path, "planner")
    if manifest_errors:
        return blocked_result("Context manifest failed schema validation.", manifest_errors)
    return {
        "status": "completed",
        "next_action": "planner",
        "summary": "Planner context manifest created.",
        "artifacts_created": [],
        "blockers": [],
        "warnings": [],
        "tokens_used": 0,
    }


def run_publication_prepare(
    *,
    task_id: str,
    project_profile: str,
    repository: Path,
    artifacts_dir: Path,
    base_branch: str,
) -> dict[str, Any]:
    include = changed_paths(repository)
    write_json(
        artifacts_dir / "change_set.json",
        {
            "target_repository": ".",
            "project_profile": project_profile,
            "task_id": task_id,
            "expected_remote": git_remote(repository),
            "include": include,
            "exclude": [],
        },
    )
    title = task_id.replace("-", " ").strip().title() or "Agent workflow update"
    write_json(
        artifacts_dir / "publication_payload.json",
        {
            "title": title,
            "body": "Prepared by the deterministic publication-prepare step from the task worktree diff.",
            "commit_message": title,
            "base_branch": base_branch or "main",
        },
    )
    return artifact_result(
        "Publication inputs prepared from the task worktree diff.",
        ["change_set.json", "publication_payload.json"],
        "publication",
    )


def run_roles(
    workflow: str = "full_agent_workflow",
    run_id: str = "",
    artifacts_dir: Path | None = None,
    dry_run: bool = False,
    task_id: str = "task",
    goal: str = "",
    project: str = "",
    repository: Path = ROOT,
    branch: str = "",
    base_branch: str = "main",
    adapter_command: str = "",
    token_budget: int = 12000,
    timeout_seconds: int = 600,
    create_task_worktree: bool = False,
) -> dict[str, Any]:
    run_id = run_id or make_run_id(workflow)
    repository = repository.resolve()
    goal = goal or task_id
    fingerprint = task_fingerprint(
        task_id=task_id,
        goal=goal,
        repository=repository,
        branch=branch,
        base_branch=base_branch,
    )
    existing_workflow = RUNS / run_id / "workflow.json"
    if existing_workflow.exists():
        try:
            existing = load_json(existing_workflow)
        except (OSError, json.JSONDecodeError, ValueError):
            existing = {}
        if existing.get("execution_status") == "completed" and existing.get("input_fingerprint") == fingerprint:
            return existing
    duplicate = find_completed_run(RUNS, fingerprint, exclude_run_id=run_id)
    if duplicate is not None:
        return {**duplicate, "deduplicated": True, "duplicate_of": duplicate.get("run_id", "")}
    layout = RunLayout.create(RUNS, run_id)
    try:
        layout.assert_artifacts_dir(artifacts_dir)
    except ValueError as exc:
        state = {
            "run_id": run_id,
            "workflow": workflow,
            "task_id": task_id,
            "execution_status": "blocked",
            "roles": [],
            "loops": initial_loops(),
            "budgets": workflow_budgets(workflow),
            "role_count": 0,
            "tokens_used": 0,
            "blockers": [str(exc)],
            "input_fingerprint": fingerprint,
        }
        write_json(layout.workflow, state)
        record_failure(layout, stage="initialization", code="NON_AUTHORITATIVE_STATE", message=str(exc))
        write_metrics(layout, state)
        return state
    run_dir = layout.root
    run_artifacts = layout.artifacts
    project_profile = project_profile_for(project)
    context_dir = layout.context
    requests_dir = layout.requests
    raw_dir = layout.raw_events
    adapter_command = effective_adapter_command(workflow, adapter_command)
    worktree, effective_branch, effective_base, setup_errors = prepare_worktree(
        repository,
        task_id,
        project,
        run_id,
        branch,
        base_branch,
        create_task_worktree,
    )
    if uses_production_codex_executor(adapter_command) and not create_task_worktree:
        setup_errors.append(
            "production Codex full workflow requires --create-worktree; implementation may not run in the source repository"
        )
    state: dict[str, Any] = {
        "run_id": run_id,
        "workflow": workflow,
        "task_id": task_id,
        "goal": goal,
        "project": project,
        "project_profile": project_profile,
        "repository": str(repository),
        "worktree": str(worktree.resolve()),
        "branch": effective_branch,
        "base_branch": effective_base,
        "dry_run": dry_run,
        "execution_status": "running",
        "roles": [],
        "artifacts_dir": str(run_artifacts),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": 0,
        "role_count": 0,
        "tokens_used": 0,
        "input_fingerprint": fingerprint,
        "executor": {
            "kind": "codex_cli" if uses_production_codex_executor(adapter_command) else "external_adapter",
            "command": adapter_command,
            "production": uses_production_codex_executor(adapter_command),
        },
        "base_branch_sha_before": git_ref_sha(repository, effective_base),
        "base_branch_sha_after": "",
        "budgets": workflow_budgets(workflow),
        "loops": initial_loops(),
    }
    write_json(layout.workflow, state)
    append_trace(layout, {"event": "workflow_started", "workflow": workflow, "task_id": task_id})
    write_metrics(layout, state)
    if setup_errors:
        state["execution_status"] = "blocked"
        state["blockers"] = setup_errors
        write_json(layout.workflow, state)
        append_trace(layout, {"event": "workflow_blocked", "blockers": setup_errors})
        record_failure(
            layout,
            stage="worktree",
            code="WORKTREE_SETUP_FAILED",
            message="Task worktree setup failed.",
            details=setup_errors,
        )
        write_metrics(layout, state)
        return state

    if uses_production_codex_executor(adapter_command):
        runtime_preflight = check_codex_runtime(repo=worktree, sandbox="read-only", timeout_seconds=min(timeout_seconds, 60))
        write_json(layout.root / "codex-runtime-preflight.json", runtime_preflight)
        if runtime_preflight.get("execution_status") != "completed":
            blockers = [str(item) for item in runtime_preflight.get("blockers", [])]
            state["execution_status"] = "blocked"
            state["blockers"] = blockers or ["Codex CLI is not available or not authenticated."]
            write_json(layout.workflow, state)
            append_trace(layout, {"event": "workflow_blocked", "blockers": state["blockers"]})
            record_failure(
                layout,
                stage="codex-preflight",
                code="CODEX_RUNTIME_BLOCKED",
                message="Real Codex executor preflight failed.",
                details=state["blockers"],
            )
            write_metrics(layout, state)
            return state

    adapter = CodexAdapter(command=adapter_command, timeout_seconds=timeout_seconds, raw_output_dir=raw_dir)
    completed_roles: list[str] = []
    role_visits: dict[str, int] = {}
    source_snapshot_before = git_snapshot(repository)
    role = ROLE_CHAIN[0]
    guard = 0
    workflow_started = time.monotonic()
    while role:
        guard += 1
        role_visits[role] = role_visits.get(role, 0) + 1
        role_started = time.monotonic()
        artifact_contents_before = file_contents_snapshot(run_artifacts)
        artifact_snapshot_before = file_snapshot(run_artifacts)
        role_repo_snapshot_before = git_snapshot(worktree)
        if guard > len(ROLE_CHAIN) * 3 or role_visits[role] > 3:
            result = blocked_result("Workflow routing exceeded the safety limit.", ["dynamic routing loop detected"])
        elif role == "issue-intake":
            result = run_issue_intake(
                run_id=run_id,
                task_id=task_id,
                goal=goal,
                project=project,
                repository=repository,
                worktree=worktree,
                branch=effective_branch,
                base_branch=effective_base,
                artifacts_dir=run_artifacts,
            )
        elif role == "context-compiler":
            result = run_context_compiler(
                run_id=run_id,
                goal=goal,
                project=project,
                worktree=worktree,
                artifacts_dir=run_artifacts,
                context_dir=context_dir,
                project_profile=project_profile,
                token_budget=token_budget,
            )
        elif role == "publication-prepare":
            result = run_publication_prepare(
                task_id=task_id,
                project_profile=project_profile,
                repository=worktree,
                artifacts_dir=run_artifacts,
                base_branch=effective_base,
            )
        elif role == "publication":
            preflight_result = preflight_role_execution(
                role=role,
                project_profile=project_profile,
                artifacts_dir=run_artifacts,
                dry_run=dry_run,
            )
            result = preflight_result or run_publication(
                run_id=run_id,
                repository=worktree,
                artifacts_dir=run_artifacts,
                dry_run=dry_run,
                timeout_seconds=timeout_seconds,
            )
        else:
            contract = role_contract(role)
            manifest_path = create_context_manifest(
                run_id=run_id,
                role=role,
                goal=goal,
                repository=worktree,
                artifacts_dir=run_artifacts,
                context_dir=context_dir,
                project=project,
                project_profile=project_profile,
                token_budget=token_budget,
                allowed_tools=role_tools(role),
                previous_roles=completed_roles,
                filesystem_access=role_filesystem_access(role),
                prompt_path=str(contract.get("prompt_path", "")),
                output_contract=str(contract.get("output_contract", "")),
                expected_artifacts=list(contract.get("expected_artifacts", [])),
            )
            manifest_errors = validate_manifest(manifest_path, role)
            if manifest_errors:
                result = blocked_result("Context manifest failed schema validation.", manifest_errors)
            else:
                preflight_result = preflight_role_execution(
                    role=role,
                    project_profile=project_profile,
                    artifacts_dir=run_artifacts,
                    dry_run=dry_run,
                )
                if preflight_result is not None:
                    result = preflight_result
                else:
                    request = build_role_request(
                        run_id=run_id,
                        role=role,
                        goal=goal,
                        repository=worktree,
                        artifacts_dir=run_artifacts,
                        context_manifest=manifest_path,
                        token_budget=token_budget,
                        timeout_seconds=timeout_seconds,
                        project_profile=project_profile,
                    )
                    write_json(requests_dir / f"{role}.json", request)
                    result = adapter.invoke(request)

        result_errors = validate_role_result(result, role)
        if result_errors:
            result = blocked_result("Role result failed schema validation.", result_errors)
        deterministic_created = ensure_deterministic_role_artifacts(role, run_artifacts, project_profile)
        if deterministic_created and result.get("status") == "completed":
            created = list(result.get("artifacts_created", []))
            for artifact in deterministic_created:
                if artifact not in created:
                    created.append(artifact)
            result["artifacts_created"] = created
        result.setdefault("duration_ms", int((time.monotonic() - role_started) * 1000))
        contract = role_contract(role)
        expected_artifacts = [
            str(item)
            for item in contract.get("expected_artifacts", [])
            if isinstance(item, str)
        ]
        artifact_ownership_errors = ownership_errors(
            role=role,
            allowed_artifacts=expected_artifacts,
            before=artifact_snapshot_before,
            after=file_snapshot(run_artifacts),
        )
        if artifact_ownership_errors:
            restore_foreign_artifacts(
                directory=run_artifacts,
                allowed_artifacts=expected_artifacts,
                before=artifact_contents_before,
            )
            result = blocked_result("Role artifact ownership validation failed.", artifact_ownership_errors)
        if role_filesystem_access(role) != "task_worktree_write" and git_snapshot(worktree) != role_repo_snapshot_before:
            result = blocked_result(
                "Read-only role changed repository contents.",
                [f"{role} changed task worktree code despite {role_filesystem_access(role)} access"],
            )
        if result.get("status") == "completed":
            artifact_errors = validate_role_artifacts(
                role=role,
                result=result,
                artifacts_dir=run_artifacts,
                worktree=worktree,
                source_repository=repository,
                source_snapshot_before=source_snapshot_before,
                create_task_worktree=create_task_worktree,
            )
            if artifact_errors:
                result = blocked_result("Role artifact validation failed.", artifact_errors)
        checkpoint = {
            "time": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "prompt_file": PROMPT_FILES.get(role, ""),
            "prompt": role_prompt(role),
            "result": result,
        }
        state["roles"].append(checkpoint)
        completed_roles.append(role)
        write_json(layout.role_results / f"{role}-{role_visits[role]}.json", checkpoint)
        append_trace(layout, {"event": "role_completed", "role": role, "result": result})
        if result.get("status") != "completed":
            record_failure(
                layout,
                stage="role",
                role=role,
                code="ROLE_NOT_COMPLETED",
                message=str(result.get("summary", "Role did not complete.")),
                details=[str(item) for item in result.get("blockers", [])],
            )

        state["role_count"] = len(state["roles"])
        state["tokens_used"] = sum(
            int(item.get("result", {}).get("tokens_used", 0))
            for item in state["roles"]
            if isinstance(item.get("result"), dict)
        )
        state["elapsed_seconds"] = int(time.monotonic() - workflow_started)
        route = decide_next_role(
            current_role=role,
            role_result=result,
            run_dir=run_dir,
            artifacts_dir=run_artifacts,
            workflow_state=state,
        )
        state["last_route"] = route
        write_json(layout.workflow, state)
        write_metrics(layout, state)
        append_trace(
            layout,
            {
                "type": "router.decision",
                "event": "router.decision",
                "run_id": run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "current_role": role,
                "next_role": route["next_role"],
                "reason": route["reason"],
                "loop": route.get("loop"),
                "publication_allowed": route["publication_allowed"],
                "warnings": route.get("warnings", []),
            },
        )
        if route["stop"]:
            if route["next_role"] == "approval-gate":
                state["execution_status"] = "awaiting_approval"
                approval = awaiting_approval_result(route["reason"], route.get("warnings", []))
                approval_checkpoint = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "role": "approval-gate",
                    "prompt_file": "",
                    "prompt": "",
                    "result": approval,
                }
                state["roles"].append(approval_checkpoint)
                state["role_count"] = len(state["roles"])
                write_json(layout.role_results / "approval-gate-1.json", approval_checkpoint)
                append_trace(layout, {"event": "workflow_awaiting_approval", "reason": route["reason"]})
                record_failure(
                    layout,
                    stage="routing",
                    code="AWAITING_APPROVAL",
                    message=route["reason"],
                    details=route.get("warnings", []),
                )
            elif route["next_role"] == "":
                state["execution_status"] = "completed"
            else:
                state["execution_status"] = "blocked"
            break
        role = route["next_role"]

    if state["execution_status"] == "running":
        state["execution_status"] = "completed"
    state["base_branch_sha_after"] = git_ref_sha(repository, effective_base)
    write_json(layout.workflow, state)
    write_metrics(layout, state)
    append_trace(layout, {"event": "workflow_finished", "execution_status": state["execution_status"]})
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default="full_agent_workflow")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--task-id", default="task")
    parser.add_argument("--goal", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--branch", default="")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--adapter-command", default="")
    parser.add_argument("--token-budget", type=int, default=12000)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--create-worktree", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = run_roles(
        args.workflow,
        args.run_id,
        args.artifacts_dir,
        args.dry_run,
        args.task_id,
        args.goal,
        args.project,
        args.repo,
        args.branch,
        args.base_branch,
        args.adapter_command,
        args.token_budget,
        args.timeout_seconds,
        args.create_worktree,
    )
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0 if state["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
