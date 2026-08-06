#!/usr/bin/env python3
"""Run the agent-role workflow through a provider-neutral runtime boundary."""

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
from urllib.parse import urlparse

from opentelemetry.trace import Status, StatusCode


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
HARNESS_ROOT = SCRIPT_DIR.parent
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

from approval_lifecycle import ApprovalError, request_approval
from context_compiler import create_context_manifest, role_capability, role_contract
from repository_registry import RepositoryRecord, find_by_remote, load_local_project_record
from runtime_contracts import contract_section, load_json, validate_contract
from runtimes import RuntimeConfigurationError, create_runtime
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
from worktree_manager import create_worktree, inspect_current_checkout, slug, use_current_checkout
from ai_harness.recovery import RecoveryCoordinator, classify_failure, load_recovery_policy
from ai_harness.recovery.checkpoints import (
    CheckpointError,
    RoleCheckpoint,
    read_checkpoint,
    resume_operation as checkpoint_resume_operation,
    write_checkpoint,
)
from ai_harness.recovery.models import FailureRecord, persist_failure
from ai_harness.observability import safe_telemetry_runtime


ROOT = HARNESS_ROOT
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


def persist_control_failure(
    layout: RunLayout,
    state: dict[str, Any],
    *,
    role: str,
    stage: str,
    kind: str,
    error_type: str,
    message: str,
) -> FailureRecord:
    """Persist a structured record for harness failures outside a role runtime."""
    failure = FailureRecord.create(
        run_id=layout.run_id,
        task_id=str(state.get("task_id", "task")),
        role=role,
        stage=stage,
        kind=kind,
        error_type=error_type,
        message=message,
        retryable=kind in {"transient", "runtime_failure", "tool_failure", "internal_error"},
        repairable=kind in {"invalid_output", "verification_failure"},
        checkpoint=f"before_{role.replace('-', '_')}",
    )
    persist_failure(layout.root, failure)
    state["failure_id"] = failure.failure_id
    state["failure_kind"] = failure.kind
    return failure


def role_checkpoint(
    *,
    run_dir: Path,
    run_id: str,
    role: str,
    state_name: str,
    attempt: int,
    worktree: Path,
    input_fingerprint: str,
    result: dict[str, Any] | None = None,
) -> None:
    output_fingerprint = ""
    artifacts: list[str] = []
    if result is not None:
        output_fingerprint = "sha256:" + hashlib.sha256(
            json.dumps(result, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
        ).hexdigest()
        artifacts = [str(item) for item in result.get("artifacts_created", []) if isinstance(item, str)]
    write_checkpoint(
        run_dir,
        RoleCheckpoint(
            run_id=run_id,
            role=role,
            state=state_name,
            attempt=max(1, attempt),
            worktree=str(worktree.resolve()),
            input_fingerprint=input_fingerprint,
            output_fingerprint=output_fingerprint,
            artifacts=artifacts,
        ),
    )


def schedule_role_recovery(
    *,
    layout: RunLayout,
    state: dict[str, Any],
    role: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    policy = load_recovery_policy()
    hint = result.get("_failure", {})
    preliminary = classify_failure(
        None,
        None,
        state,
        result,
        layout.artifacts,
        run_id=layout.run_id,
        task_id=str(state.get("task_id", "task")),
        role=role,
        stage="runtime_execute",
    )
    configured = policy.for_kind(preliminary.kind)
    recovery = state.get("recovery", {})
    if not isinstance(recovery, dict):
        recovery = {}
    by_kind = recovery.get("attempts_by_kind", {})
    if not isinstance(by_kind, dict):
        by_kind = {}
    attempt = int(by_kind.get(preliminary.kind, 0) or 0) + 1
    if isinstance(hint, dict) and int(hint.get("repair_attempts", 0) or 0) >= configured.max_attempts:
        attempt = max(attempt, configured.max_attempts + 1)
    checkpoint = f"before_{role.replace('-', '_')}"
    failure = classify_failure(
        None,
        None,
        state,
        result,
        layout.artifacts,
        run_id=layout.run_id,
        task_id=str(state.get("task_id", "task")),
        role=role,
        stage="runtime_execute",
        checkpoint=checkpoint,
        attempt=attempt,
        max_attempts=configured.max_attempts,
    )
    decision = RecoveryCoordinator().decide(failure, state, policy)
    persist_failure(layout.root, failure)
    by_kind[failure.kind] = attempt
    recovery.update(
        {
            "started_at": float(recovery.get("started_at", time.time()) or time.time()),
            "attempts": int(recovery.get("attempts", 0) or 0) + 1,
            "consecutive_failures": int(recovery.get("consecutive_failures", 0) or 0) + 1,
            "resume_attempts": int(recovery.get("resume_attempts", 0) or 0) + (1 if decision.action == "resume" else 0),
            "attempts_by_kind": by_kind,
        }
    )
    recovery["elapsed_seconds"] = int(max(0, time.time() - float(recovery["started_at"])))
    state.update(
        {
            "execution_status": decision.next_status,
            "current_role": role,
            "resume_role": role,
            "resume_from": checkpoint,
            "failure_id": failure.failure_id,
            "failure_kind": failure.kind,
            "recovery_action": decision.action,
            "recovery_reason": decision.reason,
            "retry_after_seconds": decision.delay_seconds,
            "recovery": recovery,
        }
    )
    return {"failure": failure.as_json(), "decision": decision.as_json()}


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


def role_budget_tokens(result: dict[str, Any]) -> int:
    """Return incremental token usage, excluding input served from cache."""
    input_tokens = result.get("input_tokens")
    cached_input_tokens = result.get("cached_input_tokens")
    output_tokens = result.get("output_tokens")
    if all(isinstance(value, int) for value in (input_tokens, cached_input_tokens, output_tokens)):
        return max(input_tokens - cached_input_tokens, 0) + output_tokens
    tokens_used = result.get("tokens_used", 0)
    return int(tokens_used) if isinstance(tokens_used, (int, float)) else 0


def resume_runtime_command(
    stored_runtime: dict[str, Any],
    *,
    runtime_command: str = "",
    adapter_command: str = "",
) -> str:
    """Reuse fixture commands only; production runtimes must reload trusted config."""
    explicit = runtime_command or adapter_command
    if explicit:
        return explicit
    if stored_runtime.get("production") is True or stored_runtime.get("provider") == "codex-cli":
        return ""
    return str(stored_runtime.get("command", ""))


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


def set_attention(
    state: dict[str, Any],
    *,
    summary: str,
    details: list[str],
    role: str,
    action: str,
) -> None:
    """Persist one concise, user-facing explanation for a paused workflow."""

    normalized = list(
        dict.fromkeys(str(item).strip() for item in details if str(item).strip())
    )
    if not normalized:
        normalized = [summary]
    state["attention"] = {
        "required": True,
        "summary": summary,
        "details": normalized,
        "role": role,
        "action": action,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state["blockers"] = normalized


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


def artifact_bytes(path: Path, *, stop_after: int) -> int:
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            total += candidate.stat().st_size
        except OSError:
            continue
        if total > stop_after:
            return total
    return total


def execute_runtime_observed(
    runtime: Any,
    *,
    run_dir: Path,
    role: str,
    context: Path,
    task: dict[str, Any],
    worktree: Path,
    artifacts: Path,
) -> dict[str, Any]:
    telemetry = safe_telemetry_runtime(run_dir=run_dir, service_name="ai-harness-role-runtime")
    try:
        with telemetry.span("ai_harness.runtime.execute", {"role": role}) as span:
            telemetry.runtime_executions_total.add(1, {"role": role})
            result = runtime.execute(
                role=role,
                context=context,
                task=task,
                worktree=worktree,
                artifacts=artifacts,
            )
            failure = result.get("_failure", {}) if isinstance(result, dict) else {}
            if isinstance(failure, dict) and failure:
                error_type = str(failure.get("error_type", "RuntimeFailure"))
                telemetry.runtime_failures_total.add(1, {"error.type": error_type})
                span.set_attribute("error.type", error_type)
                span.set_status(Status(StatusCode.ERROR))
                if "timeout" in error_type.lower():
                    telemetry.runtime_timeouts_total.add(1, {"error.type": error_type})
                    with telemetry.span("ai_harness.runtime.timeout", {"role": role, "error.type": error_type}):
                        pass
            return result
    finally:
        try:
            telemetry.shutdown()
        except Exception:
            pass


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
            "frontend_dev_command": str(frontend.get("dev_command", "")) if isinstance(frontend, dict) else "",
            "frontend_local_url": str(frontend.get("local_url", "")) if isinstance(frontend, dict) else "",
            "frontend_network_scope": list(frontend.get("network_scope", [])) if isinstance(frontend, dict) and isinstance(frontend.get("network_scope", []), list) else [],
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
    errors.extend(validate_verifier_artifact(role, artifacts_dir))
    return errors


def validate_verifier_artifact(role: str, artifacts_dir: Path) -> list[str]:
    artifact_names = {
        "security-agent": "security.json",
        "frontend-qa-agent": "frontend_qa.json",
        "architecture-consistency-agent": "architecture_consistency.json",
        "semantic-conflict-agent": "semantic_conflict.json",
        "reviewer": "review.json",
    }
    artifact_name = artifact_names.get(role)
    if artifact_name is None or not (artifacts_dir / artifact_name).exists():
        return []
    try:
        artifact = load_json(artifacts_dir / artifact_name)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"{artifact_name} verifier artifact is invalid: {exc}"]
    verdict = artifact.get("verdict")
    blockers = artifact.get("blockers", [])
    repair_required = artifact.get("repair_required")
    errors: list[str] = []
    if verdict == "works" and (repair_required is not False or blockers):
        errors.append(f"{artifact_name}: works requires repair_required=false and no blockers")
    if verdict == "broken" and (repair_required is not True or not blockers):
        errors.append(f"{artifact_name}: broken requires repair_required=true and blockers")
    if verdict == "unavailable" and not blockers:
        errors.append(f"{artifact_name}: unavailable requires a blocker explaining missing verification")
    if role == "security-agent":
        severity = artifact.get("highest_severity")
        if verdict == "works" and severity not in {"none", "low"}:
            errors.append("security.json: works permits only none or low highest_severity")
        if verdict == "broken" and severity not in {"medium", "high", "critical"}:
            errors.append("security.json: broken requires medium, high, or critical highest_severity")
    if role != "frontend-qa-agent" or verdict != "works":
        return errors
    local_url = str(artifact.get("local_url", ""))
    host = (urlparse(local_url).hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        errors.append("frontend_qa.json: works requires a loopback local_url")
    if artifact.get("evidence_collected") is not True:
        errors.append("frontend_qa.json: works requires evidence_collected=true")
    screenshots = artifact.get("screenshots", [])
    if not isinstance(screenshots, list) or not screenshots:
        errors.append("frontend_qa.json: works requires at least one screenshot")
    else:
        for value in screenshots:
            if not isinstance(value, str) or not value.startswith("frontend-evidence/") or not safe_artifact_name(value):
                errors.append(f"frontend_qa.json: unsafe screenshot path {value!r}")
                continue
            if not (artifacts_dir / value).is_file():
                errors.append(f"frontend_qa.json: screenshot is missing: {value}")
    dev_server = artifact.get("dev_server")
    if not isinstance(dev_server, dict) or not {"command", "status"}.issubset(dev_server):
        errors.append("frontend_qa.json: works requires dev_server command and status")
    evidence = artifact.get("evidence", [])
    if not isinstance(evidence, list) or not evidence:
        errors.append("frontend_qa.json: works requires interaction evidence")
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
        "max_repair_iterations": 12,
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
        for name in ("quality_repair", "review_repair", "ci_repair", "frontend_verification_repair")
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
    record = find_by_remote(remote) if remote else None
    if record is None:
        try:
            record = load_local_project_record(repository)
        except ValueError as exc:
            return None, [str(exc)]
    if project and record is None:
        return None, [
            "repository is not centrally registered or locally initialized; run `agent init` first"
        ]
    if project and record is not None:
        expected = record.project_profile if record.source == "local_project_config" else record.repository_id
        if expected != project:
            return record, [f"repository identity is {expected!r}, expected {project!r}"]
    return record, []


def prepare_worktree(
    repository: Path,
    task_id: str,
    project: str,
    run_id: str,
    branch: str,
    base_branch: str,
    create_task_worktree: bool,
    current_branch: bool = False,
) -> tuple[Path, str, str, list[str]]:
    record, errors = resolve_registry_record(repository, project)
    if errors:
        return repository, "", "", errors
    effective_base = record.base_branch if record is not None else base_branch
    effective_branch = branch or f"issue/{slug(task_id)}"
    if current_branch:
        result = use_current_checkout(
            repository,
            task_id,
            effective_branch,
            effective_base,
            run_id,
            runs_dir=RUNS,
            require_clean=True,
        )
        if result.get("execution_status") != "completed":
            return repository, effective_branch, effective_base, [str(item) for item in result.get("errors", [])]
        return repository, effective_branch, effective_base, []
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


def frontend_qa_unavailable_result(artifacts_dir: Path, warnings: list[str]) -> dict[str, Any]:
    existing_artifact = artifacts_dir / "frontend_qa.json"
    if existing_artifact.exists():
        try:
            existing = load_json(existing_artifact)
        except (OSError, json.JSONDecodeError, ValueError):
            existing = {}
        if (
            existing.get("verdict") == "works"
            and not validate_verifier_artifact("frontend-qa-agent", artifacts_dir)
        ):
            result = completed_result(
                "Existing run-scoped frontend QA evidence remains valid.",
                "continue",
            )
            result["artifacts_created"] = []
            return result
    write_json(
        artifacts_dir / "frontend_qa.json",
        {
            "verdict": "unavailable",
            "expected": ["A running local development environment and Playwright interaction evidence."],
            "observed": ["Browser or Playwright capability is unavailable in this runtime."],
            "evidence": [],
            "repair_required": False,
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
    repository: Path | None = None,
) -> dict[str, Any] | None:
    outcome = role_tool_preflight(
        role=role,
        allowed_tools=role_tools(role),
        project_profile=project_profile,
        dry_run=dry_run,
        run_dir=artifacts_dir.parent,
        repository=repository,
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
        "summary": "Deterministic Issue Intake harness stage recorded.",
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
    timeout_seconds: int = 1800,
    create_task_worktree: bool = False,
    current_branch: bool = False,
    resume: bool = False,
    runtime_provider: str = "",
    runtime_command: str = "",
) -> dict[str, Any]:
    run_id = run_id or make_run_id(workflow)
    existing_workflow = RUNS / run_id / "workflow.json"
    existing: dict[str, Any] = {}
    if existing_workflow.exists():
        try:
            existing = load_json(existing_workflow)
        except (OSError, json.JSONDecodeError, ValueError):
            existing = {}
    if resume:
        if not existing:
            return {"run_id": run_id, "execution_status": "blocked", "blockers": ["resume state is missing"]}
        if existing.get("execution_status") not in {"resuming", "running"}:
            return {
                **existing,
                "execution_status": "blocked",
                "blockers": [f"run cannot resume from {existing.get('execution_status')!r}"],
            }
        repository = Path(str(existing.get("repository", ""))).resolve()
        task_id = str(existing.get("task_id", task_id))
        goal = str(existing.get("goal", goal or task_id))
        project = str(existing.get("project", project))
        branch = str(existing.get("branch", branch))
        base_branch = str(existing.get("base_branch", base_branch))
        workflow = str(existing.get("workflow", workflow))
        fingerprint = str(existing.get("input_fingerprint", ""))
    else:
        repository = repository.resolve()
        goal = goal or task_id
        fingerprint = task_fingerprint(
            task_id=task_id,
            goal=goal,
            repository=repository,
            branch=branch,
            base_branch=base_branch,
            workspace_mode="current_branch" if current_branch else "isolated",
        )
        if existing.get("execution_status") == "completed" and existing.get("input_fingerprint") == fingerprint:
            return existing
        if isinstance(existing.get("runtime"), dict) or isinstance(existing.get("executor"), dict):
            if existing.get("input_fingerprint") == fingerprint:
                return existing
            return {
                **existing,
                "execution_status": "blocked",
                "blockers": ["run id already belongs to a different or unfinished workflow; use --resume"],
            }
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
    context_dir = layout.context
    requests_dir = layout.requests
    raw_dir = layout.raw_events
    resume_cached_result: dict[str, Any] | None = None
    checkpoint_problem = ""
    if resume:
        state = existing
        state["tokens_used"] = sum(
            role_budget_tokens(item.get("result", {}))
            for item in state.get("roles", [])
            if isinstance(item, dict) and isinstance(item.get("result"), dict)
        )
        project_profile = str(state.get("project_profile", project_profile_for(project)))
        worktree = Path(str(state.get("worktree", ""))).resolve()
        effective_branch = str(state.get("branch", branch))
        effective_base = str(state.get("base_branch", base_branch))
        current_branch = str(state.get("workspace_mode", "isolated")) == "current_branch"
        stored_runtime = state.get("runtime", state.get("executor", {}))
        if not isinstance(stored_runtime, dict):
            stored_runtime = {}
        stored_provider = str(stored_runtime.get("provider", ""))
        if not stored_provider and stored_runtime.get("kind") == "codex_cli":
            stored_provider = "codex-cli"
        selected_provider = runtime_provider or stored_provider
        selected_command = resume_runtime_command(
            stored_runtime,
            runtime_command=runtime_command,
            adapter_command=adapter_command,
        )
        setup_errors = []
        if not repository.is_dir() or not worktree.is_dir():
            setup_errors.append("resume repository or task worktree is missing")
        if current_branch and repository.is_dir():
            checkout = inspect_current_checkout(
                repository,
                expected_branch=effective_branch,
                protected_branches={effective_base, "main", "master", "trunk"},
                require_clean=False,
            )
            setup_errors.extend(str(item) for item in checkout.get("errors", []))
        role = str(state.pop("resume_role", ""))
        if not role:
            last_route = state.get("last_route", {})
            if isinstance(last_route, dict):
                candidate = str(last_route.get("next_role", ""))
                if candidate not in {"", "approval-gate", "blocked"}:
                    role = candidate
        if role not in ROLE_CHAIN:
            setup_errors.append("resume checkpoint role is invalid or missing")
        else:
            try:
                stored_checkpoint = read_checkpoint(run_dir, role)
                if stored_checkpoint is not None:
                    if Path(stored_checkpoint.worktree).resolve() != worktree:
                        raise CheckpointError("checkpoint worktree does not match authoritative workflow")
                    operation = checkpoint_resume_operation(stored_checkpoint)
                    if operation == "validate_output":
                        pending_path = layout.role_results / f"{role}-pending.json"
                        pending = load_json(pending_path)
                        cached = pending.get("result") if isinstance(pending, dict) else None
                        if not isinstance(cached, dict):
                            raise CheckpointError("validation checkpoint is missing cached role output")
                        resume_cached_result = cached
                    elif operation == "next_role":
                        last_route = state.get("last_route", {})
                        next_role = str(last_route.get("next_role", "")) if isinstance(last_route, dict) else ""
                        if next_role in ROLE_CHAIN:
                            role = next_role
            except (CheckpointError, OSError, json.JSONDecodeError, ValueError) as exc:
                checkpoint_problem = str(exc)
                setup_errors.append(checkpoint_problem)
        state["execution_status"] = "running"
        state["resume_count"] = int(state.get("resume_count", 0)) + 1
        state["resumed_at"] = datetime.now(timezone.utc).isoformat()
        create_task_worktree = True
        append_trace(layout, {"event": "workflow_resumed", "role": role})
    else:
        project_profile = project_profile_for(project)
        selected_provider = runtime_provider
        selected_command = runtime_command or adapter_command
        worktree, effective_branch, effective_base, setup_errors = prepare_worktree(
            repository,
            task_id,
            project,
            run_id,
            branch,
            base_branch,
            create_task_worktree,
            current_branch,
        )
    runtime = None
    try:
        runtime = create_runtime(
            provider=selected_provider,
            command=selected_command,
            timeout_seconds=timeout_seconds,
            raw_output_dir=raw_dir,
        )
    except RuntimeConfigurationError as exc:
        setup_errors.append(str(exc))
    if runtime is not None and runtime.descriptor.production and not (create_task_worktree or current_branch):
        setup_errors.append(
            "production runtime requires an isolated task worktree or explicit --current-branch mode"
        )
    if resume:
        if runtime is not None:
            state["runtime"] = runtime.descriptor.as_json()
            state.pop("executor", None)
    else:
        state = {
            "run_id": run_id,
            "workflow": workflow,
            "task_id": task_id,
            "goal": goal,
            "project": project,
            "project_profile": project_profile,
            "repository": str(repository),
            "worktree": str(worktree.resolve()),
            "workspace_mode": "current_branch" if current_branch else "isolated",
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
            "runtime": runtime.descriptor.as_json() if runtime is not None else {
                "provider": selected_provider,
                "kind": "runtime_adapter",
                "transport": "invalid",
                "production": False,
                "command": selected_command,
                "api_required": False,
            },
            "base_branch_sha_before": git_ref_sha(repository, effective_base),
            "base_branch_sha_after": "",
            "budgets": workflow_budgets(workflow),
            "loops": initial_loops(),
        }
        role = ROLE_CHAIN[0]
        append_trace(layout, {"event": "workflow_started", "workflow": workflow, "task_id": task_id})
    write_json(layout.workflow, state)
    write_metrics(layout, state)
    if checkpoint_problem:
        failure = FailureRecord.create(
            run_id=run_id,
            task_id=task_id,
            role=role or "resume",
            stage="checkpoint_resume",
            kind="unrecoverable",
            error_type="CorruptedCheckpoint",
            message=checkpoint_problem,
            retryable=False,
            repairable=False,
            checkpoint=str(state.get("resume_from", "")),
        )
        persist_failure(layout.root, failure)
        state.update(
            {
                "execution_status": "dead_letter",
                "failure_id": failure.failure_id,
                "failure_kind": failure.kind,
                "recovery_action": "dead_letter",
                "recovery_reason": "checkpoint cannot be resumed safely",
                "blockers": [checkpoint_problem],
            }
        )
        write_json(layout.workflow, state)
        write_metrics(layout, state)
        return state
    if setup_errors:
        state["execution_status"] = "blocked"
        set_attention(
            state,
            summary="Task workspace setup needs attention.",
            details=setup_errors,
            role="issue-intake",
            action="fix_then_retry",
        )
        write_json(layout.workflow, state)
        append_trace(layout, {"event": "workflow_blocked", "blockers": setup_errors})
        record_failure(
            layout,
            stage="worktree",
            code="WORKTREE_SETUP_FAILED",
            message="Task worktree setup failed.",
            details=setup_errors,
        )
        persist_control_failure(
            layout, state, role="issue-intake", stage="worktree", kind="tool_failure",
            error_type="WorktreeSetupFailed", message="; ".join(setup_errors),
        )
        write_json(layout.workflow, state)
        write_metrics(layout, state)
        return state

    assert runtime is not None
    runtime_preflight = runtime.preflight(worktree=worktree, timeout_seconds=timeout_seconds)
    runtime_preflight["provider"] = runtime.descriptor.provider
    write_json(layout.root / "runtime-preflight.json", runtime_preflight)
    if runtime_preflight.get("execution_status") != "completed":
        blockers = [str(item) for item in runtime_preflight.get("blockers", [])]
        state["execution_status"] = "blocked"
        set_attention(
            state,
            summary="Configured runtime is unavailable.",
            details=blockers or ["Configured runtime is unavailable."],
            role="issue-intake",
            action="fix_then_retry",
        )
        write_json(layout.workflow, state)
        append_trace(layout, {"event": "workflow_blocked", "blockers": state["blockers"]})
        record_failure(
            layout,
            stage="runtime-preflight",
            code="RUNTIME_PREFLIGHT_BLOCKED",
            message="Configured runtime preflight failed.",
            details=state["blockers"],
        )
        persist_control_failure(
            layout, state, role="issue-intake", stage="runtime-preflight", kind="runtime_failure",
            error_type="RuntimePreflightBlocked", message="; ".join(state["blockers"]),
        )
        write_json(layout.workflow, state)
        write_metrics(layout, state)
        return state
    prior_roles = [
        str(item.get("role", ""))
        for item in state.get("roles", [])
        if isinstance(item, dict) and isinstance(item.get("role"), str)
    ]
    completed_roles = [
        str(item.get("role", ""))
        for item in state.get("roles", [])
        if isinstance(item, dict)
        and isinstance(item.get("result"), dict)
        and item["result"].get("status") == "completed"
    ]
    role_visits: dict[str, int] = {
        name: prior_roles.count(name) for name in set(prior_roles)
    }
    source_snapshot_before = git_snapshot(repository)
    guard = 0
    elapsed_before_resume = int(state.get("elapsed_seconds", 0) or 0)
    workflow_started = time.monotonic()
    while role:
        guard += 1
        role_visits[role] = role_visits.get(role, 0) + 1
        role_started = time.monotonic()
        state["current_role"] = role
        state["resume_role"] = role
        write_json(layout.workflow, state)
        role_checkpoint(
            run_dir=run_dir,
            run_id=run_id,
            role=role,
            state_name="role_pending",
            attempt=role_visits[role],
            worktree=worktree,
            input_fingerprint=fingerprint,
        )
        role_checkpoint(
            run_dir=run_dir,
            run_id=run_id,
            role=role,
            state_name="role_running",
            attempt=role_visits[role],
            worktree=worktree,
            input_fingerprint=fingerprint,
        )
        artifact_contents_before = file_contents_snapshot(run_artifacts)
        artifact_snapshot_before = file_snapshot(run_artifacts)
        role_repo_snapshot_before = git_snapshot(worktree)
        max_roles = int(state.get("budgets", {}).get("max_roles", 40))
        if resume_cached_result is not None:
            result = resume_cached_result
            resume_cached_result = None
        elif guard > max_roles:
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
                repository=worktree,
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
                    repository=worktree,
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
                    result = execute_runtime_observed(
                        runtime,
                        run_dir=run_dir,
                        role=role,
                        context=manifest_path,
                        task=request,
                        worktree=worktree,
                        artifacts=run_artifacts,
                    )

        artifact_limit = load_recovery_policy().runtime_limits.max_artifact_bytes
        used_artifact_bytes = artifact_bytes(run_artifacts, stop_after=artifact_limit)
        if used_artifact_bytes > artifact_limit:
            result = blocked_result(
                "Run artifacts exceeded the configured byte limit.",
                [f"artifact bytes {used_artifact_bytes} exceed limit {artifact_limit}"],
            )
            result["_failure"] = {
                "kind": "unrecoverable",
                "error_type": "ArtifactLimitExceeded",
                "message": "Run artifacts exceeded the configured byte limit.",
            }

        role_checkpoint(
            run_dir=run_dir,
            run_id=run_id,
            role=role,
            state_name="role_output_received",
            attempt=role_visits[role],
            worktree=worktree,
            input_fingerprint=fingerprint,
            result=result,
        )
        if not isinstance(result.get("_failure"), dict):
            write_json(
                layout.role_results / f"{role}-pending.json",
                {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "role": role,
                    "state": "role_output_received",
                    "result": result,
                },
            )
        role_checkpoint(
            run_dir=run_dir,
            run_id=run_id,
            role=role,
            state_name="role_validating",
            attempt=role_visits[role],
            worktree=worktree,
            input_fingerprint=fingerprint,
            result=result,
        )
        result_errors = validate_role_result(result, role)
        if result_errors:
            result = blocked_result("Role result failed schema validation.", result_errors)
            result["_failure"] = {
                "kind": "invalid_output",
                "error_type": "InvalidStructuredOutput",
                "message": "Role result failed schema validation.",
            }
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
        owned_patterns = [
            str(item)
            for item in contract.get("owned_artifact_patterns", [])
            if isinstance(item, str)
        ]
        artifact_ownership_errors = ownership_errors(
            role=role,
            allowed_artifacts=[*expected_artifacts, *owned_patterns],
            before=artifact_snapshot_before,
            after=file_snapshot(run_artifacts),
        )
        if artifact_ownership_errors:
            restore_foreign_artifacts(
                directory=run_artifacts,
                allowed_artifacts=[*expected_artifacts, *owned_patterns],
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
                result["_failure"] = {
                    "kind": "invalid_output",
                    "error_type": "InvalidArtifactOutput",
                    "message": "Role artifact validation failed.",
                }
        checkpoint = {
            "time": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "execution_kind": str(role_contract(role).get("execution_kind", "llm_role")),
            "llm_invoked": role in ADAPTER_ROLES,
            "prompt_file": PROMPT_FILES.get(role, ""),
            "prompt": role_prompt(role),
            "result": result,
        }
        state["roles"].append(checkpoint)
        if result.get("status") == "completed":
            completed_roles.append(role)
        write_json(layout.role_results / f"{role}-{role_visits[role]}.json", checkpoint)
        append_trace(layout, {"event": "role_completed", "role": role, "result": result})
        if isinstance(result.get("_failure"), dict):
            pending_path = layout.role_results / f"{role}-pending.json"
            if pending_path.exists():
                pending_path.unlink()
            role_checkpoint(
                run_dir=run_dir,
                run_id=run_id,
                role=role,
                state_name="role_running",
                attempt=role_visits[role],
                worktree=worktree,
                input_fingerprint=fingerprint,
            )
            recovery_event = schedule_role_recovery(
                layout=layout,
                state=state,
                role=role,
                result=result,
            )
            state["role_count"] = len(state["roles"])
            state["tokens_used"] = sum(
                role_budget_tokens(item.get("result", {}))
                for item in state["roles"]
                if isinstance(item, dict) and isinstance(item.get("result"), dict)
            )
            state["elapsed_seconds"] = elapsed_before_resume + int(time.monotonic() - workflow_started)
            write_json(layout.workflow, state)
            write_metrics(layout, state)
            append_trace(layout, {"event": "role_recovery_scheduled", "role": role, **recovery_event})
            if state["execution_status"] == "awaiting_approval":
                set_attention(
                    state,
                    summary=str(result.get("summary", state.get("recovery_reason", "User input is required."))),
                    details=[str(item) for item in result.get("blockers", [])],
                    role=role,
                    action="answer_or_approve",
                )
                try:
                    request_approval(
                        layout.root,
                        reason=str(state["attention"]["summary"]),
                    )
                except ApprovalError as exc:
                    state["execution_status"] = "blocked"
                    set_attention(
                        state,
                        summary="The approval request could not be created.",
                        details=[f"approval request failed: {exc}"],
                        role="approval-gate",
                        action="fix_then_retry",
                    )
                    persist_control_failure(
                        layout, state, role="approval-gate", stage="approval",
                        kind="internal_error", error_type="ApprovalRequestFailed", message=str(exc),
                    )
                    write_json(layout.workflow, state)
            state["base_branch_sha_after"] = git_ref_sha(repository, effective_base)
            write_json(layout.workflow, state)
            write_metrics(layout, state)
            return state
        if result.get("status") != "completed":
            record_failure(
                layout,
                stage="role",
                role=role,
                code="ROLE_NOT_COMPLETED",
                message=str(result.get("summary", "Role did not complete.")),
                details=[str(item) for item in result.get("blockers", [])],
            )
            persist_control_failure(
                layout, state, role=role, stage="role", kind="human_input_required",
                error_type="RoleNotCompleted", message=str(result.get("summary", "Role did not complete.")),
            )
        else:
            role_checkpoint(
                run_dir=run_dir,
                run_id=run_id,
                role=role,
                state_name="role_completed",
                attempt=role_visits[role],
                worktree=worktree,
                input_fingerprint=fingerprint,
                result=result,
            )
            pending_path = layout.role_results / f"{role}-pending.json"
            if pending_path.exists():
                pending_path.unlink()

        state["role_count"] = len(state["roles"])
        state["tokens_used"] = sum(
            role_budget_tokens(item.get("result", {}))
            for item in state["roles"]
            if isinstance(item.get("result"), dict)
        )
        state["elapsed_seconds"] = elapsed_before_resume + int(time.monotonic() - workflow_started)
        route = decide_next_role(
            current_role=role,
            role_result=result,
            run_dir=run_dir,
            artifacts_dir=run_artifacts,
            workflow_state=state,
        )
        route_errors = validate_contract(
            route,
            load_json(SCHEMAS / "workflow_route.schema.json"),
            "workflow_route",
        )
        if route_errors:
            route = {
                "next_role": "approval-gate",
                "reason": "Deterministic router returned an invalid route contract.",
                "stop": True,
                "publication_allowed": False,
                "loop": None,
                "warnings": route_errors,
            }
            record_failure(
                layout,
                stage="routing",
                code="INVALID_ROUTE_CONTRACT",
                message=route["reason"],
                details=route_errors,
            )
            persist_control_failure(
                layout, state, role=role, stage="routing", kind="internal_error",
                error_type="InvalidRouteContract", message="; ".join(route_errors),
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
                attention_details = [
                    *[str(item) for item in result.get("blockers", [])],
                    *[str(item) for item in route.get("warnings", [])],
                ]
                attention_summary = (
                    str(result.get("summary", "")).strip()
                    if result.get("status") in {"blocked", "failed", "awaiting_approval"}
                    else ""
                ) or route["reason"]
                attention_action = (
                    "answer_or_approve"
                    if result.get("status") in {"blocked", "failed", "awaiting_approval"}
                    else "approve"
                )
                set_attention(
                    state,
                    summary=attention_summary,
                    details=attention_details,
                    role=role,
                    action=attention_action,
                )
                approval_checkpoint = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "role": "approval-gate",
                    "execution_kind": "harness_stage",
                    "llm_invoked": False,
                    "prompt_file": "",
                    "prompt": "",
                    "result": approval,
                }
                state["roles"].append(approval_checkpoint)
                state["role_count"] = len(state["roles"])
                write_json(layout.role_results / "approval-gate-1.json", approval_checkpoint)
                write_json(layout.workflow, state)
                try:
                    request_approval(layout.root, reason=attention_summary)
                except ApprovalError as exc:
                    state["execution_status"] = "blocked"
                    set_attention(
                        state,
                        summary="The approval request could not be created.",
                        details=[f"approval request failed: {exc}"],
                        role="approval-gate",
                        action="fix_then_retry",
                    )
                    record_failure(
                        layout,
                        stage="approval",
                        code="APPROVAL_REQUEST_FAILED",
                        message="Could not create scoped approval request.",
                        details=[str(exc)],
                    )
                    persist_control_failure(
                        layout, state, role="approval-gate", stage="approval",
                        kind="internal_error", error_type="ApprovalRequestFailed", message=str(exc),
                    )
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
                set_attention(
                    state,
                    summary=route["reason"],
                    details=[route["reason"], *route.get("warnings", [])],
                    role=role,
                    action="fix_then_retry",
                )
                append_trace(layout, {"event": "workflow_blocked", "reason": route["reason"]})
                record_failure(
                    layout,
                    stage="routing",
                    code="ROUTER_BLOCKED",
                    message=route["reason"],
                    details=route.get("warnings", []),
                )
                persist_control_failure(
                    layout, state, role=role, stage="routing", kind="human_input_required",
                    error_type="RouterBlocked", message=route["reason"],
                )
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
    parser.add_argument("--runtime-provider", default="")
    parser.add_argument("--runtime-command", default="")
    parser.add_argument("--adapter-command", default="", help=argparse.SUPPRESS)
    parser.add_argument("--token-budget", type=int, default=12000)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--create-worktree", action="store_true")
    parser.add_argument("--current-branch", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = run_roles(
        workflow=args.workflow,
        run_id=args.run_id,
        artifacts_dir=args.artifacts_dir,
        dry_run=args.dry_run,
        task_id=args.task_id,
        goal=args.goal,
        project=args.project,
        repository=args.repo,
        branch=args.branch,
        base_branch=args.base_branch,
        adapter_command=args.adapter_command,
        token_budget=args.token_budget,
        timeout_seconds=args.timeout_seconds,
        create_task_worktree=args.create_worktree,
        current_branch=args.current_branch,
        resume=args.resume,
        runtime_provider=args.runtime_provider,
        runtime_command=args.runtime_command,
    )
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0 if state["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
