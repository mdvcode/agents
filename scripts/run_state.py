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


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".agent-runs"


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
    for checkpoint in state.get("roles", []):
        if not isinstance(checkpoint, dict):
            continue
        result = checkpoint.get("result", {})
        if not isinstance(result, dict):
            result = {}
        roles.append(
            {
                "role": str(checkpoint.get("role", "")),
                "status": str(result.get("status", "")),
                "duration_ms": int(result.get("duration_ms", 0) or 0),
                "tokens_used": int(result.get("tokens_used", 0) or 0),
                "input_tokens": int(result.get("input_tokens", 0) or 0),
                "cached_input_tokens": int(result.get("cached_input_tokens", 0) or 0),
                "output_tokens": int(result.get("output_tokens", 0) or 0),
                "reasoning_output_tokens": int(result.get("reasoning_output_tokens", 0) or 0),
            }
        )
    write_json(
        layout.metrics,
        {
            "run_id": layout.run_id,
            "execution_status": str(state.get("execution_status", "")),
            "role_count": len(roles),
            "tokens_used": sum(role["tokens_used"] for role in roles),
            "duration_ms": sum(role["duration_ms"] for role in roles),
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
