#!/usr/bin/env python3
"""Run the agent-role workflow with strict adapter and run-scoped artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
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
from context_compiler import create_context_manifest, role_capability, role_contract
from repository_registry import RepositoryRecord, find_by_remote
from validate_artifacts import validate_required as validate_artifact_required
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
    "publication",
]
INTERNAL_ROLES = {"issue-intake", "context-compiler"}
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
KNOWN_PROJECT_PROFILES = {"agent_workspace", "django", "flowfox"}


def make_run_id(workflow: str) -> str:
    return datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S.%fZ-{workflow}")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_trace(run_dir: Path, event: dict[str, Any]) -> None:
    with (run_dir / "workflow_trace.jsonl").open("a", encoding="utf-8") as trace:
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
    for artifact in result.get("artifacts_created", []):
        if not safe_artifact_name(artifact):
            errors.append(f"{role}: artifacts_created contains unsafe path {artifact!r}")
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
    if role == "implementation-agent" and create_task_worktree and worktree.resolve() != source_repository.resolve():
        source_snapshot_after = git_snapshot(source_repository)
        if source_snapshot_after != source_snapshot_before:
            errors.append("implementation-agent changed the source repository instead of only the task worktree")
    return errors


def next_role_name(current_role: str, result: dict[str, Any]) -> str:
    action = str(result.get("next_action", "continue"))
    if action in {"blocked", "awaiting_approval", "completed"}:
        return ""
    if action in ROLE_CHAIN and action != current_role:
        return action
    index = ROLE_CHAIN.index(current_role)
    return ROLE_CHAIN[index + 1] if index + 1 < len(ROLE_CHAIN) else ""


def git_remote(repo: Path) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
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
        "artifacts_created": ["publication.json", "verdict.json"],
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
    run_dir = RUNS / run_id
    run_artifacts = (artifacts_dir or run_dir / "artifacts").resolve()
    project_profile = project_profile_for(project)
    context_dir = run_dir / "context"
    requests_dir = run_dir / "requests"
    raw_dir = run_dir / "raw"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_artifacts.mkdir(parents=True, exist_ok=True)
    goal = goal or task_id
    repository = repository.resolve()
    worktree, effective_branch, effective_base, setup_errors = prepare_worktree(
        repository,
        task_id,
        project,
        run_id,
        branch,
        base_branch,
        create_task_worktree,
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
    }
    write_json(run_dir / "agent_workflow.json", state)
    append_trace(run_dir, {"event": "workflow_started", "workflow": workflow, "task_id": task_id})
    if setup_errors:
        state["execution_status"] = "blocked"
        state["blockers"] = setup_errors
        write_json(run_dir / "agent_workflow.json", state)
        append_trace(run_dir, {"event": "workflow_blocked", "blockers": setup_errors})
        return state

    adapter = CodexAdapter(command=adapter_command, timeout_seconds=timeout_seconds, raw_output_dir=raw_dir)
    completed_roles: list[str] = []
    role_visits: dict[str, int] = {}
    source_snapshot_before = git_snapshot(repository)
    role = ROLE_CHAIN[0]
    guard = 0
    while role:
        guard += 1
        role_visits[role] = role_visits.get(role, 0) + 1
        role_started = time.monotonic()
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
        elif role == "publication":
            result = run_publication(
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
        result.setdefault("duration_ms", int((time.monotonic() - role_started) * 1000))
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
        write_json(run_artifacts / f"{role}.json", checkpoint)
        append_trace(run_dir, {"event": "role_completed", "role": role, "result": result})

        if result["status"] in {"blocked", "failed"} or result["next_action"] == "blocked":
            state["execution_status"] = "blocked"
            break
        if result["status"] == "awaiting_approval" or result["next_action"] == "awaiting_approval":
            state["execution_status"] = "awaiting_approval"
            break
        if role == "risk-classifier" and high_risk_requested_approval(run_artifacts):
            state["execution_status"] = "awaiting_approval"
            approval = awaiting_approval_result(
                "High risk requires human approval before publication.",
                ["risk.json classified the task as high"],
            )
            state["roles"].append(
                {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "role": "approval-gate",
                    "prompt_file": "",
                    "prompt": "",
                    "result": approval,
                }
            )
            write_json(run_artifacts / "approval-gate.json", state["roles"][-1])
            append_trace(run_dir, {"event": "workflow_awaiting_approval", "reason": "high risk"})
            break
        role = next_role_name(role, result)

    if state["execution_status"] == "running":
        state["execution_status"] = "completed"
    write_json(run_dir / "agent_workflow.json", state)
    append_trace(run_dir, {"event": "workflow_finished", "execution_status": state["execution_status"]})
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
