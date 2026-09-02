#!/usr/bin/env python3
"""Authoritative run-state layout, ownership, metrics, and failure helpers."""

from __future__ import annotations

import hashlib
import json
import fnmatch
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ai_harness.execution_accounting import (
    accounted_checkpoints,
    accounted_tokens_used,
    role_entry_invoked_model,
    safe_int,
)


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".agent-runs"


def continuation_project_identity(workflow: dict[str, Any]) -> dict[str, str]:
    """Return validated optional project identity for a queued continuation.

    Runs created before first-class Projects do not contain either field and
    remain resumable.  New runs persist both fields as one identity pair so a
    continuation cannot silently detach from its registered project.
    """

    has_project_id = "project_id" in workflow
    has_project_key = "project_key" in workflow
    if not has_project_id and not has_project_key:
        return {}
    if has_project_id != has_project_key:
        raise ValueError("workflow project identity is incomplete")
    project_id = workflow.get("project_id")
    project_key = workflow.get("project_key")
    if not isinstance(project_id, str) or not isinstance(project_key, str):
        raise ValueError("workflow project identity must contain strings")
    project_id = project_id.strip()
    project_key = project_key.strip()
    if not project_id or not project_key:
        raise ValueError("workflow project identity is incomplete")
    if (
        len(project_id) > 64
        or project_id.strip("-") != project_id
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in project_id)
    ):
        raise ValueError("workflow project id is invalid")
    if (
        len(project_key) != 64
        or any(character not in "0123456789abcdef" for character in project_key)
    ):
        raise ValueError("workflow project key is invalid")
    return {"project_id": project_id, "project_key": project_key}


def continuation_attachment_payload(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return validated attachment metadata for a continuation queue record."""

    manifest = workflow.get("input_manifest", "")
    digest = workflow.get("input_manifest_sha256", "")
    count = workflow.get("attachment_count", 0)
    consent = workflow.get("attachment_runtime_consent", False)
    if not any((manifest, digest, count, consent)):
        return {}
    if not isinstance(manifest, str) or not manifest.strip():
        raise ValueError("workflow attachment manifest path is missing")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("workflow attachment manifest digest is invalid")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5:
        raise ValueError("workflow attachment count is invalid")
    if consent is not True:
        raise ValueError("workflow attachment runtime consent is missing")
    return {
        "input_manifest": manifest.strip(),
        "input_manifest_sha256": digest,
        "attachment_count": count,
        "attachment_runtime_consent": True,
    }


@dataclass(frozen=True)
class RunLayout:
    """All mutable state for one task run."""

    run_id: str
    root: Path
    workflow: Path
    context: Path
    requests: Path
    role_results: Path
    raw_events: Path
    artifacts: Path
    metrics: Path
    errors: Path
    audit_log: Path

    @classmethod
    def create(cls, runs_dir: Path, run_id: str) -> "RunLayout":
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError(f"unsafe run id: {run_id!r}")
        root = (runs_dir / run_id).resolve()
        if root.parent != runs_dir.resolve():
            raise ValueError(f"run directory escapes .agent-runs: {root}")
        layout = cls(
            run_id=run_id,
            root=root,
            workflow=root / "workflow.json",
            context=root / "context-manifests",
            requests=root / "role-requests",
            role_results=root / "role-results",
            raw_events=root / "raw-events",
            artifacts=root / "artifacts",
            metrics=root / "metrics.json",
            errors=root / "errors.jsonl",
            audit_log=root / "audit-log.jsonl",
        )
        for directory in (
            layout.root,
            layout.context,
            layout.requests,
            layout.role_results,
            layout.raw_events,
            layout.artifacts,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return layout

    def assert_artifacts_dir(self, artifacts_dir: Path | None) -> None:
        if artifacts_dir is None:
            return
        if artifacts_dir.resolve() != self.artifacts.resolve():
            raise ValueError(
                "artifacts_dir must be the authoritative run path "
                f"{self.artifacts}; mutable artifact mirrors are forbidden"
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False) + "\n")


def file_snapshot(directory: Path) -> dict[str, str]:
    """Hash every run artifact so foreign writes can be detected after a role."""
    if not directory.exists():
        return {}
    snapshot: dict[str, str] = {}
    for path in sorted(candidate for candidate in directory.rglob("*") if candidate.is_file()):
        snapshot[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def file_contents_snapshot(directory: Path) -> dict[str, bytes]:
    if not directory.exists():
        return {}
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(candidate for candidate in directory.rglob("*") if candidate.is_file())
    }


def changed_snapshot_paths(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def ownership_errors(
    *,
    role: str,
    allowed_artifacts: Iterable[str],
    before: dict[str, str],
    after: dict[str, str],
) -> list[str]:
    allowed = tuple(allowed_artifacts)
    return [
        f"{role} modified artifact owned by another role: {path}"
        for path in sorted(changed_snapshot_paths(before, after))
        if not any(path == pattern or fnmatch.fnmatch(path, pattern) for pattern in allowed)
    ]


def restore_foreign_artifacts(
    *,
    directory: Path,
    allowed_artifacts: Iterable[str],
    before: dict[str, bytes],
) -> list[str]:
    """Restore or remove every changed artifact not owned by the active role."""
    before_hashes = {path: hashlib.sha256(content).hexdigest() for path, content in before.items()}
    after_hashes = file_snapshot(directory)
    allowed = tuple(allowed_artifacts)
    foreign = {
        path
        for path in changed_snapshot_paths(before_hashes, after_hashes)
        if not any(path == pattern or fnmatch.fnmatch(path, pattern) for pattern in allowed)
    }
    for relative in foreign:
        path = directory / relative
        if relative in before:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before[relative])
        elif path.exists():
            path.unlink()
    return sorted(foreign)


def record_failure(
    layout: RunLayout,
    *,
    stage: str,
    code: str,
    message: str,
    role: str = "",
    details: Iterable[str] = (),
) -> None:
    append_jsonl(
        layout.errors,
        {
            "time": utc_now(),
            "stage": stage,
            "role": role,
            "code": code,
            "message": message,
            "details": list(details),
        },
    )


def write_metrics(layout: RunLayout, state: dict[str, Any]) -> None:
    roles: list[dict[str, Any]] = []
    checkpoints = accounted_checkpoints(state.get("roles", []))
    for checkpoint in checkpoints:
        result = checkpoint.get("result", {})
        if not isinstance(result, dict):
            result = {}
        profile = checkpoint.get("execution_profile", {})
        if not isinstance(profile, dict):
            profile = {}
        roles.append(
            {
                "role": str(checkpoint.get("role", "")),
                "status": str(result.get("status", "")),
                "duration_ms": safe_int(result.get("duration_ms", 0)),
                "tokens_used": safe_int(result.get("tokens_used", 0)),
                "input_tokens": safe_int(result.get("input_tokens", 0)),
                "cached_input_tokens": safe_int(result.get("cached_input_tokens", 0)),
                "output_tokens": safe_int(result.get("output_tokens", 0)),
                "reasoning_output_tokens": safe_int(result.get("reasoning_output_tokens", 0)),
                "execution_profile": str(
                    result.get("execution_profile", profile.get("execution_profile", ""))
                ),
                "model": str(result.get("model", profile.get("model", ""))),
                "reasoning_effort": str(
                    result.get("reasoning_effort", profile.get("reasoning_effort", ""))
                ),
                "escalation_level": int(
                    result.get("escalation_level", profile.get("escalation_level", 0)) or 0
                ),
            }
        )
    input_tokens = sum(role["input_tokens"] for role in roles)
    cached_input_tokens = sum(role["cached_input_tokens"] for role in roles)
    output_tokens = sum(role["output_tokens"] for role in roles)
    model_calls = sum(
        role_entry_invoked_model(checkpoint)
        for checkpoint in checkpoints
    )
    plan: dict[str, Any] = {}
    plan_path = state.get("execution_plan_path")
    if isinstance(plan_path, str) and Path(plan_path).is_file():
        try:
            value = json.loads(Path(plan_path).read_text(encoding="utf-8"))
            plan = value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            plan = {}
    cache_statuses: list[str] = []
    for manifest_path in layout.context.glob("*.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cache = manifest.get("context_cache", {}) if isinstance(manifest, dict) else {}
        if isinstance(cache, dict) and isinstance(cache.get("status"), str):
            cache_statuses.append(str(cache["status"]))
    cache_hits = sum(status in {"hit", "compatible_hit"} for status in cache_statuses)
    skipped_roles = {
        str(role)
        for role in plan.get("skipped_roles", [])
        if isinstance(role, str)
    } | {
        str(role)
        for role in state.get("budget_skipped_roles", [])
        if isinstance(role, str)
    }
    repair_attempts = sum(
        int(value.get("iterations", 0) or 0)
        for value in state.get("loops", {}).values()
        if isinstance(value, dict)
    ) if isinstance(state.get("loops"), dict) else 0
    completed = state.get("execution_status") == "completed"
    elapsed_seconds = int(state.get("elapsed_seconds", 0) or 0)
    uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
    total_success_tokens = uncached_input_tokens + output_tokens if completed else 0
    deterministic_checks = 0
    for artifact_name, field in (("quality.json", "checks"), ("security.json", "evidence")):
        artifact_path = layout.artifacts / artifact_name
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        values = artifact.get(field, []) if isinstance(artifact, dict) else []
        deterministic_checks += len(values) if isinstance(values, list) else 0
    approval_requests = 0
    approvals_path = layout.raw_events / "approvals.jsonl"
    if approvals_path.is_file():
        try:
            approval_requests = sum(
                json.loads(line).get("event") == "approval.requested"
                for line in approvals_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except (OSError, json.JSONDecodeError):
            approval_requests = 0
    time_to_accepted_pr: int | None = None
    publication_path = layout.artifacts / "publication.json"
    if publication_path.is_file():
        try:
            publication = json.loads(publication_path.read_text(encoding="utf-8"))
            if isinstance(publication, dict) and publication.get("pr_created_or_updated") is True:
                started = datetime.fromisoformat(str(state.get("started_at", "")).replace("Z", "+00:00"))
                accepted_value = str(
                    publication.get("pr_published_at", publication.get("completed_at", ""))
                )
                accepted = (
                    datetime.fromisoformat(accepted_value.replace("Z", "+00:00"))
                    if accepted_value
                    else datetime.fromtimestamp(publication_path.stat().st_mtime, tz=timezone.utc)
                )
                time_to_accepted_pr = max(0, int((accepted - started).total_seconds()))
        except (OSError, ValueError, json.JSONDecodeError):
            time_to_accepted_pr = None
    write_json(
        layout.metrics,
        {
            "run_id": layout.run_id,
            "execution_status": str(state.get("execution_status", "")),
            "role_count": len(roles),
            "tokens_used": accounted_tokens_used(state.get("roles", [])),
            "duration_ms": sum(role["duration_ms"] for role in roles),
            "model_calls_per_task": model_calls,
            "model_calls_per_successful_task": model_calls if completed else 0,
            "input_tokens_per_task": input_tokens,
            "uncached_input_tokens_per_task": uncached_input_tokens,
            "output_tokens_per_task": output_tokens,
            "cached_input_ratio": (
                round(cached_input_tokens / input_tokens, 6) if input_tokens else 0.0
            ),
            "context_cache_hit_rate": (
                round(cache_hits / len(cache_statuses), 6) if cache_statuses else 0.0
            ),
            "roles_executed_per_task": len(roles),
            "roles_skipped_per_task": len(skipped_roles),
            "deterministic_checks_per_task": deterministic_checks,
            "model_escalations_per_task": sum(role["escalation_level"] > 0 for role in roles),
            "repair_attempts_per_task": repair_attempts,
            "time_to_success": elapsed_seconds if completed else None,
            "time_to_accepted_pr": time_to_accepted_pr,
            "tokens_to_success": total_success_tokens,
            "successful_task_token_cost": total_success_tokens,
            "successful_task_latency": elapsed_seconds if completed else None,
            "human_interventions_per_task": approval_requests,
            "roles": roles,
        },
    )


def task_fingerprint(
    *,
    task_id: str,
    goal: str,
    repository: Path,
    branch: str,
    base_branch: str,
    workspace_mode: str = "worktree",
    workflow_mode: str = "auto",
    input_manifest_sha256: str = "",
) -> str:
    payload = {
        "task_id": task_id,
        "goal": goal,
        "repository": str(repository.resolve()),
        "branch": branch,
        "base_branch": base_branch,
        "workspace_mode": workspace_mode,
        "workflow_mode": workflow_mode,
    }
    if input_manifest_sha256:
        payload["input_manifest_sha256"] = input_manifest_sha256
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def find_completed_run(runs_dir: Path, fingerprint: str, *, exclude_run_id: str = "") -> dict[str, Any] | None:
    """Return the newest completed run for identical task input."""
    if not runs_dir.exists():
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for workflow_path in runs_dir.glob("*/workflow.json"):
        if workflow_path.parent.name == exclude_run_id:
            continue
        try:
            state = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        if state.get("input_fingerprint") != fingerprint or state.get("execution_status") != "completed":
            continue
        candidates.append((workflow_path.stat().st_mtime, state))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]
