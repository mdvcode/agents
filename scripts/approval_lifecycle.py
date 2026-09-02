#!/usr/bin/env python3
"""Scoped approval lifecycle for supervised workflow resume."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from runtime_contracts import load_json as load_schema, validate_contract
from security_approval import security_scope
from task_queue import DEFAULT_DB, TaskQueue, TaskRecord
from verifier_environment import verifier_artifact_unavailable
from workflow_router import review_repair_extension_scope_valid
from ai_harness.recovery.checkpoints import RoleCheckpoint, read_checkpoint, write_checkpoint
from ai_harness.recovery.policy import load_recovery_policy


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".agent-runs"
APPROVAL_SCHEMA = ROOT / "schemas" / "approval.schema.json"
TERMINAL_APPROVAL_STATES = {"rejected", "expired", "consumed"}


class ApprovalError(ValueError):
    """Raised when an approval transition or scope is invalid."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ApprovalError(f"{path.name} must contain an object")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_approval(run_dir: Path, approval: dict[str, Any]) -> None:
    errors = validate_contract(approval, load_schema(APPROVAL_SCHEMA), "approval")
    if errors:
        raise ApprovalError("invalid approval artifact: " + "; ".join(errors))
    write_json_atomic(run_dir / "artifacts" / "approval.json", approval)


@contextmanager
def approval_lock(run_dir: Path):
    path = run_dir / "artifacts" / "approval.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_event(run_dir: Path, event: str, approval: dict[str, Any]) -> None:
    path = run_dir / "raw-events" / "approvals.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": iso(utc_now()),
        "event": event,
        "approval_id": approval.get("approval_id", ""),
        "status": approval.get("status", ""),
        "checkpoint_fingerprint": approval.get("checkpoint_fingerprint", ""),
        "decided_by": approval.get("decided_by", ""),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_error(run_dir: Path, *, code: str, message: str) -> None:
    path = run_dir / "errors.jsonl"
    entry = {
        "time": iso(utc_now()),
        "stage": "approval",
        "role": "approval-gate",
        "code": code,
        "message": message,
        "details": [],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _scope_integer(scope: dict[str, Any], key: str) -> int:
    value = scope.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApprovalError(f"approval scope {key} must be an integer")
    return value


def canonical_scope(scope: dict[str, Any]) -> dict[str, Any]:
    actions = sorted({str(item) for item in scope.get("actions", []) if isinstance(item, str)})
    paths = sorted({str(item) for item in scope.get("paths", []) if isinstance(item, str)})
    finding_ids = sorted(
        {str(item) for item in scope.get("finding_ids", []) if isinstance(item, str)}
    )
    gate = str(scope.get("gate", ""))
    risk_class = str(scope.get("risk_class", ""))
    security_fingerprint = str(scope.get("security_fingerprint", ""))
    verifier_fingerprint = str(scope.get("verifier_fingerprint", ""))
    loop_name = str(scope.get("loop_name", ""))
    at_iteration = _scope_integer(scope, "at_iteration")
    max_iterations = _scope_integer(scope, "max_iterations")
    failure_fingerprint = str(scope.get("failure_fingerprint", ""))
    diff_fingerprint = str(scope.get("diff_fingerprint", ""))
    additional_attempts = _scope_integer(scope, "additional_attempts")
    return {
        "actions": actions,
        "paths": paths,
        "gate": gate,
        "risk_class": risk_class,
        "finding_ids": finding_ids,
        "security_fingerprint": security_fingerprint,
        "verifier_fingerprint": verifier_fingerprint,
        "loop_name": loop_name,
        "at_iteration": at_iteration,
        "max_iterations": max_iterations,
        "failure_fingerprint": failure_fingerprint,
        "diff_fingerprint": diff_fingerprint,
        "additional_attempts": additional_attempts,
    }


def scope_covers(requested: dict[str, Any], approved: dict[str, Any]) -> bool:
    """Require an exact scope match so an approval cannot silently expand authority."""
    return canonical_scope(requested) == canonical_scope(approved)


def checkpoint_role(workflow: dict[str, Any]) -> str:
    roles = workflow.get("roles", [])
    if not isinstance(roles, list):
        return ""
    for checkpoint in reversed(roles):
        if isinstance(checkpoint, dict) and checkpoint.get("role") != "approval-gate":
            role = checkpoint.get("role")
            return str(role) if isinstance(role, str) else ""
    return ""


def checkpoint_fingerprint(workflow: dict[str, Any], role: str, reason: str) -> str:
    payload: dict[str, Any] = {
        "run_id": workflow.get("run_id", ""),
        "input_fingerprint": workflow.get("input_fingerprint", ""),
        "role": role,
        "reason": reason,
        "role_count": workflow.get("role_count", 0),
        "tokens_used": workflow.get("tokens_used", 0),
    }
    if "checkout_path" in workflow or "task_branch" in workflow:
        payload.update(
            {
                "checkout_path": workflow.get("checkout_path", workflow.get("worktree", "")),
                "task_branch": workflow.get("task_branch", workflow.get("branch", "")),
                "branch_owner_run_id": workflow.get("branch_owner_run_id", workflow.get("run_id", "")),
            }
        )
    else:
        payload.update(
            {
                "worktree": workflow.get("worktree", ""),
                "branch": workflow.get("branch", ""),
            }
        )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def one_time_repair_extension_scope(
    workflow: dict[str, Any], verifier: dict[str, Any]
) -> dict[str, Any]:
    """Return one exact scope for an exhausted, progressing review repair."""

    last_route = workflow.get("last_route", {})
    loop = last_route.get("loop", {}) if isinstance(last_route, dict) else {}
    if not isinstance(loop, dict):
        return {}
    name = str(loop.get("name", ""))
    iteration = int(loop.get("iteration", 0) or 0)
    maximum = int(loop.get("max_iterations", 0) or 0)
    if (
        name != "review_repair"
        or last_route.get("next_role") != "approval-gate"
        or last_route.get("stop") is not True
        or not maximum
        or iteration != maximum
        or loop.get("progress_detected") is not True
        or str(verifier.get("verdict", "")).lower() != "broken"
        or verifier_artifact_unavailable(verifier)
    ):
        return {}
    loops = workflow.get("loops", {})
    stored = loops.get(name, {}) if isinstance(loops, dict) else {}
    if (
        not isinstance(stored, dict)
        or int(stored.get("iterations", 0) or 0) != iteration
        or int(stored.get("max_iterations", 0) or 0) != maximum
        or stored.get("last_failure_fingerprint") != loop.get("failure_fingerprint")
        or stored.get("last_diff_fingerprint") != loop.get("diff_fingerprint")
        or stored.get("progress_detected") is not True
        or int(stored.get("extensions_used", 0) or 0) >= 1
    ):
        return {}
    failure_fingerprint = str(loop.get("failure_fingerprint", ""))
    diff_fingerprint = str(loop.get("diff_fingerprint", ""))
    if not all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (failure_fingerprint, diff_fingerprint)
    ):
        return {}
    return {
        "loop_name": name,
        "at_iteration": iteration,
        "max_iterations": maximum,
        "failure_fingerprint": failure_fingerprint,
        "diff_fingerprint": diff_fingerprint,
        "additional_attempts": 1,
    }


def default_scope(workflow: dict[str, Any], role: str) -> dict[str, Any]:
    changed = workflow.get("changed_files", [])
    paths = [str(item) for item in changed if isinstance(item, str)] if isinstance(changed, list) else []
    risk_class = str(workflow.get("risk_class", ""))
    risk_path = Path(str(workflow.get("artifacts_dir", ""))) / "risk.json"
    if not risk_class and risk_path.is_file():
        risk_class = str(read_json(risk_path).get("risk_class", ""))
    actions = ["resume_workflow"]
    if role == "risk-classifier" and risk_class == "high":
        actions.append("patch_high_risk")
    security_path = Path(str(workflow.get("artifacts_dir", ""))) / "security.json"
    if role == "security-agent" and security_path.is_file():
        security = read_json(security_path)
        if security.get("status") in {"fail", "blocked"} or security.get("verdict") == "broken":
            actions.append("accept_security_finding")
            security_details = security_scope(security)
        else:
            security_details = {}
    else:
        security_details = {}
    verifier_artifacts = {
        "architecture-consistency-agent": "architecture_consistency.json",
        "semantic-conflict-agent": "semantic_conflict.json",
        "reviewer": "review.json",
    }
    verifier_name = verifier_artifacts.get(role)
    verifier_path = Path(str(workflow.get("artifacts_dir", ""))) / str(verifier_name or "")
    if verifier_name and verifier_path.is_file():
        verifier = read_json(verifier_path)
        if verifier_artifact_unavailable(verifier):
            actions.append("accept_unavailable_verification")
            extension_details = {}
        else:
            extension_details = one_time_repair_extension_scope(workflow, verifier)
            if extension_details:
                actions.append("extend_review_repair_once")
        verifier_details = {
            "verifier_fingerprint": hashlib.sha256(
                json.dumps(verifier, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            **extension_details,
        }
    else:
        verifier_details = {}
    return canonical_scope(
        {
            "actions": actions,
            "paths": paths,
            "gate": role,
            "risk_class": risk_class,
            **security_details,
            **verifier_details,
        }
    )


def request_approval(
    run_dir: Path,
    *,
    reason: str,
    scope: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    if ttl_seconds is None:
        ttl_seconds = load_recovery_policy().runtime_limits.approval_timeout_seconds
    if ttl_seconds <= 0 or ttl_seconds > 86400:
        raise ApprovalError("approval ttl_seconds must be between 1 and 86400")
    with approval_lock(run_dir):
        workflow = read_json(run_dir / "workflow.json")
        if workflow.get("execution_status") != "awaiting_approval":
            raise ApprovalError("workflow must be awaiting_approval before requesting approval")
        role = checkpoint_role(workflow)
        if not role:
            raise ApprovalError("approval checkpoint role is missing")
        now = utc_now()
        requested_scope = canonical_scope(scope or default_scope(workflow, role))
        if (
            "extend_review_repair_once" in requested_scope.get("actions", [])
            and requested_scope != default_scope(workflow, role)
        ):
            raise ApprovalError(
                "one-time review repair must match the exact exhausted verifier scope"
            )
        fingerprint = checkpoint_fingerprint(workflow, role, reason)
        approval_path = run_dir / "artifacts" / "approval.json"
        if approval_path.exists():
            current = expire_if_needed(run_dir, read_json(approval_path), now)
            if current.get("status") in {"pending", "approved"}:
                if (
                    current.get("checkpoint_fingerprint") == fingerprint
                    and current.get("requested_scope") == requested_scope
                ):
                    return current
                raise ApprovalError("a different approval request is already active")
        approval = {
            "run_id": str(workflow.get("run_id", run_dir.name)),
            "approval_id": secrets.token_hex(16),
            "status": "pending",
            "requested_scope": requested_scope,
            "approved_scope": {},
            "checkpoint_role": role,
            "checkpoint_fingerprint": fingerprint,
            "requested_at": iso(now),
            "expires_at": iso(now + timedelta(seconds=ttl_seconds)),
            "decided_at": "",
            "decided_by": "",
            "reason": reason,
            "decision_reason": "",
            "resume_count": 0,
        }
        write_approval(run_dir, approval)
        append_event(run_dir, "approval.requested", approval)
        return approval


def expire_if_needed(run_dir: Path, approval: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    current = now or utc_now()
    expires_at = datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00"))
    if approval.get("status") in {"pending", "approved"} and current >= expires_at:
        approval["status"] = "expired"
        approval["decided_at"] = iso(current)
        approval["decision_reason"] = "approval expired"
        write_approval(run_dir, approval)
        workflow = read_json(run_dir / "workflow.json")
        workflow["execution_status"] = "blocked"
        workflow["blockers"] = ["approval expired"]
        write_json_atomic(run_dir / "workflow.json", workflow)
        queue_path = run_dir.parent.parent / ".agent-queue" / "tasks.db"
        if queue_path.is_file():
            TaskQueue(queue_path).mark_approval_expired(str(workflow.get("run_id", run_dir.name)))
        append_event(run_dir, "approval.expired", approval)
        append_error(run_dir, code="APPROVAL_EXPIRED", message="The scoped approval expired before resume.")
    return approval


def approve_run(
    run_dir: Path,
    *,
    actor: str,
    scope: dict[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if not actor.strip():
        raise ApprovalError("approval actor is required")
    with approval_lock(run_dir):
        approval = expire_if_needed(run_dir, read_json(run_dir / "artifacts" / "approval.json"))
        if approval.get("status") != "pending":
            raise ApprovalError(f"approval is not pending: {approval.get('status')}")
        approved_scope = canonical_scope(scope or approval["requested_scope"])
        if not scope_covers(approval["requested_scope"], approved_scope):
            raise ApprovalError("approved scope does not exactly match requested scope")
        approval.update(
            {
                "status": "approved",
                "approved_scope": approved_scope,
                "decided_at": iso(utc_now()),
                "decided_by": actor,
                "decision_reason": reason,
            }
        )
        write_approval(run_dir, approval)
        append_event(run_dir, "approval.approved", approval)
        return approval


def reject_run(run_dir: Path, *, actor: str, reason: str) -> dict[str, Any]:
    if not actor.strip() or not reason.strip():
        raise ApprovalError("rejection actor and reason are required")
    with approval_lock(run_dir):
        approval = expire_if_needed(run_dir, read_json(run_dir / "artifacts" / "approval.json"))
        if approval.get("status") not in {"pending", "approved"}:
            raise ApprovalError(f"approval cannot be rejected from {approval.get('status')}")
        approval.update(
            {
                "status": "rejected",
                "decided_at": iso(utc_now()),
                "decided_by": actor,
                "decision_reason": reason,
            }
        )
        workflow = read_json(run_dir / "workflow.json")
        workflow["execution_status"] = "blocked"
        workflow["blockers"] = [f"approval rejected: {reason}"]
        write_json_atomic(run_dir / "workflow.json", workflow)
        write_approval(run_dir, approval)
        append_event(run_dir, "approval.rejected", approval)
        append_error(run_dir, code="APPROVAL_REJECTED", message=f"The scoped approval was rejected: {reason}")
        return approval


def _matching_consumed_grant(workflow: dict[str, Any], approval: dict[str, Any]) -> bool:
    approval_id = str(approval.get("approval_id", ""))
    grants = workflow.get("approval_grants", [])
    return (
        workflow.get("execution_status") == "resuming"
        and isinstance(grants, list)
        and any(isinstance(item, dict) and item.get("approval_id") == approval_id for item in grants)
    )


def _resolve_approved_attention(workflow: dict[str, Any]) -> None:
    attention = workflow.pop("attention", None)
    if not isinstance(attention, dict):
        return
    active_values = {str(attention.get("summary", "")).strip()}
    details = attention.get("details", [])
    if isinstance(details, list):
        active_values.update(str(item).strip() for item in details)
    active_values.discard("")
    blockers = workflow.get("blockers", [])
    if isinstance(blockers, list):
        workflow["blockers"] = [
            item for item in blockers if str(item).strip() not in active_values
        ]
    history = workflow.get("attention_history", [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            **attention,
            "required": False,
            "resolved_at": iso(utc_now()),
            "resolution": "approval_consumed",
        }
    )
    workflow["attention_history"] = history[-50:]


def _reset_checkpoint_for_resume(run_dir: Path, role: str) -> None:
    """Rerun a paused role after its blocking condition was approved or repaired."""

    checkpoint = read_checkpoint(run_dir, role)
    if checkpoint is None or checkpoint.state in {"role_pending", "role_running"}:
        return
    write_checkpoint(
        run_dir,
        RoleCheckpoint(
            run_id=checkpoint.run_id,
            role=checkpoint.role,
            state="role_pending",
            attempt=checkpoint.attempt,
            worktree=checkpoint.worktree,
            input_fingerprint=checkpoint.input_fingerprint,
        ),
    )


def _role_result(workflow: dict[str, Any], role: str) -> dict[str, Any]:
    roles = workflow.get("roles", [])
    if not isinstance(roles, list):
        return {}
    for entry in reversed(roles):
        if not isinstance(entry, dict) or entry.get("role") != role:
            continue
        result = entry.get("result", {})
        return result if isinstance(result, dict) else {}
    return {}


def _approved_review_extension_valid(
    run_dir: Path,
    workflow: dict[str, Any],
    role: str,
    scope: dict[str, Any],
) -> bool:
    """Require a completed verifier checkpoint and an exact live extension scope."""

    checkpoint = read_checkpoint(run_dir, role)
    artifacts_dir = Path(str(workflow.get("artifacts_dir", "")))
    return bool(
        checkpoint is not None
        and checkpoint.state == "role_completed"
        and artifacts_dir.is_dir()
        and review_repair_extension_scope_valid(
            state=workflow,
            role_result=_role_result(workflow, role),
            artifacts_dir=artifacts_dir,
            current_role=role,
            scope=scope,
        )
    )


def _exclude_approval_wait_from_recovery(
    workflow: dict[str, Any],
    approval: dict[str, Any],
    *,
    resumed_at: datetime,
) -> None:
    """Keep human decision time outside the automated recovery budget."""

    recovery = workflow.get("recovery")
    if not isinstance(recovery, dict):
        return
    started_at = recovery.get("started_at")
    if not isinstance(started_at, (int, float)) or isinstance(started_at, bool):
        return
    try:
        requested_at = datetime.fromisoformat(
            str(approval.get("requested_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return
    wait_seconds = max(0.0, (resumed_at - requested_at).total_seconds())
    shifted_started_at = min(resumed_at.timestamp(), float(started_at) + wait_seconds)
    recovery["started_at"] = shifted_started_at
    recovery["elapsed_seconds"] = int(
        max(0.0, resumed_at.timestamp() - shifted_started_at)
    )


def _prepare_resume_locked(run_dir: Path) -> dict[str, Any]:
    approval = expire_if_needed(run_dir, read_json(run_dir / "artifacts" / "approval.json"))
    workflow = read_json(run_dir / "workflow.json")
    if approval.get("status") == "consumed":
        if int(approval.get("resume_count", 0) or 0) != 1 or not _matching_consumed_grant(workflow, approval):
            raise ApprovalError("consumed approval does not match the durable workflow grant")
        return {"approval": approval, "workflow": workflow, "already_consumed": True}
    if approval.get("status") != "approved":
        raise ApprovalError(f"run requires an unexpired approved scope, got {approval.get('status')}")
    if not scope_covers(approval["requested_scope"], approval["approved_scope"]):
        raise ApprovalError("stored approved scope no longer matches requested scope")
    role = str(approval.get("checkpoint_role", ""))
    actual = checkpoint_fingerprint(workflow, role, str(approval.get("reason", "")))
    if workflow.get("execution_status") == "awaiting_approval" and actual != approval.get("checkpoint_fingerprint"):
        raise ApprovalError("workflow checkpoint changed after approval request")
    if workflow.get("execution_status") not in {"awaiting_approval", "resuming"}:
        raise ApprovalError("workflow is not awaiting approval")
    if not _matching_consumed_grant(workflow, approval):
        resumed_at = utc_now()
        workflow["execution_status"] = "resuming"
        workflow["resume_role"] = role
        _resolve_approved_attention(workflow)
        approved_scope = approval.get("approved_scope", {})
        approved_actions = approved_scope.get("actions", [])
        extension_requested = "extend_review_repair_once" in approved_actions
        if extension_requested and not _approved_review_extension_valid(
            run_dir,
            workflow,
            role,
            approved_scope,
        ):
            raise ApprovalError(
                "one-time review repair scope no longer matches the completed verifier checkpoint"
            )
        if not extension_requested:
            _reset_checkpoint_for_resume(run_dir, role)
        _exclude_approval_wait_from_recovery(
            workflow,
            approval,
            resumed_at=resumed_at,
        )
        workflow["approval_override"] = {
            "approval_id": approval["approval_id"],
            "gate": role,
            "scope": approval["approved_scope"],
        }
        grants = workflow.get("approval_grants", [])
        if not isinstance(grants, list):
            grants = []
        if not any(
            isinstance(item, dict) and item.get("approval_id") == approval["approval_id"]
            for item in grants
        ):
            grants.append(
                {
                    "approval_id": approval["approval_id"],
                    "gate": role,
                    "scope": approval["approved_scope"],
                    "checkpoint_fingerprint": approval["checkpoint_fingerprint"],
                    "granted_at": approval["decided_at"],
                    "reason": approval["reason"],
                }
            )
        workflow["approval_grants"] = grants
        # Workflow authority is written first. If the process dies next, a retry
        # can reconcile the still-approved artifact without applying the grant twice.
        write_json_atomic(run_dir / "workflow.json", workflow)
    approval["status"] = "consumed"
    approval["resume_count"] = 1
    write_approval(run_dir, approval)
    append_event(run_dir, "approval.consumed", approval)
    return {"approval": approval, "workflow": workflow, "already_consumed": False}


def prepare_resume(run_dir: Path) -> dict[str, Any]:
    with approval_lock(run_dir):
        return _prepare_resume_locked(run_dir)


def resume_run(run_dir: Path, *, queue: TaskQueue) -> tuple[dict[str, Any], TaskRecord]:
    """Consume an approval and enqueue continuation from the same run/worktree."""
    with approval_lock(run_dir):
        result = _prepare_resume_locked(run_dir)
        workflow = result["workflow"]
        approval = result["approval"]
        record = queue.enqueue(
            task_key=f"resume:{run_dir.name}:{approval['approval_id']}",
            payload={
                "task_id": str(workflow.get("task_id", "task")),
                "goal": str(workflow.get("goal", workflow.get("task_id", "task"))),
                "project": str(workflow.get("project", "agent_workspace")),
                "repository": str(workflow.get("repository", "")),
                "branch": str(workflow.get("task_branch", workflow.get("branch", ""))),
                "base_branch": str(workflow.get("base_branch", "main")),
                "workspace_mode": str(workflow.get("workspace_mode", "worktree")),
                "checkout_path": str(workflow.get("checkout_path", workflow.get("worktree", ""))),
                "task_branch": str(workflow.get("task_branch", workflow.get("branch", ""))),
                "base_sha": str(workflow.get("base_sha", "")),
                "branch_owner_run_id": str(workflow.get("branch_owner_run_id", run_dir.name)),
                "runtime_provider": str(
                    workflow.get("runtime", {}).get("provider", "codex-sdk")
                    if isinstance(workflow.get("runtime"), dict)
                    else "codex-sdk"
                ),
                "run_id": run_dir.name,
                "source": "approval",
                "event_id": str(approval["approval_id"]),
            },
            priority=100,
            max_retries=2,
            run_id=run_dir.name,
            supersede_awaiting_approval=True,
        )
        if not result["already_consumed"]:
            append_event(run_dir, "workflow.resume_queued", approval)
        return result, record


def expire_approvals(runs_dir: Path = RUNS_DIR) -> list[str]:
    expired: list[str] = []
    if not runs_dir.exists():
        return expired
    for path in runs_dir.glob("*/artifacts/approval.json"):
        run_dir = path.parents[1]
        approval = read_json(path)
        before = approval.get("status")
        after = expire_if_needed(run_dir, approval).get("status")
        if before != after and after == "expired":
            expired.append(run_dir.name)
    return sorted(expired)


def run_path(run_id: str) -> Path:
    if not run_id or "/" in run_id or ".." in run_id:
        raise ApprovalError("invalid run id")
    path = (RUNS_DIR / run_id).resolve()
    if path.parent != RUNS_DIR.resolve() or not path.is_dir():
        raise ApprovalError("run does not exist")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("approve", "reject", "resume"):
        command = subparsers.add_parser(name)
        command.add_argument("run_id")
        command.add_argument("--actor", default="")
        command.add_argument("--reason", default="")
        command.add_argument("--scope", default="", help="JSON approval scope")
        if name == "resume":
            command.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers.add_parser("expire")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "expire":
        print(json.dumps({"expired": expire_approvals()}, indent=2))
        return 0
    path = run_path(args.run_id)
    try:
        scope = json.loads(args.scope) if args.scope else None
        if scope is not None and not isinstance(scope, dict):
            raise ApprovalError("--scope must be a JSON object")
        if args.command == "approve":
            result = approve_run(path, actor=args.actor, scope=scope, reason=args.reason)
        elif args.command == "reject":
            result = reject_run(path, actor=args.actor, reason=args.reason)
        else:
            transition, record = resume_run(path, queue=TaskQueue(args.db))
            result = {**transition, "queue_task": record.__dict__}
    except (ApprovalError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
