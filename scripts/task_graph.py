#!/usr/bin/env python3
"""Bounded parent/child task orchestration and safe patch joins."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from event_ingestion import enqueue_envelope, normalize_event
from task_queue import TaskQueue


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".agent-runs"
MAX_CHILDREN = 3
MAX_GRAPH_DEPTH = 2
MAX_CHILD_TOKENS = 40_000
MAX_CHILD_DURATION_SECONDS = 900
ALLOWED_RELATIONS = {"repair", "investigation", "test", "implementation"}
ALLOWED_DEPENDENCIES = {"blocking", "non_blocking"}
SPAWNING_ROLES = {"implementation-agent", "ci-repair-agent"}


class TaskGraphError(ValueError):
    """Raised when child work violates graph or workspace invariants."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


@contextmanager
def graph_lock(run_dir: Path) -> Iterator[None]:
    lock_path = run_dir / ".children.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def safe_allowed_paths(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 50:
        raise TaskGraphError("child allowed_paths must be a list with at most 50 entries")
    normalized: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise TaskGraphError("child allowed_paths must contain only strings")
        candidate = raw.strip().replace("\\", "/").strip("/")
        path = PurePosixPath(candidate)
        if not candidate or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise TaskGraphError(f"unsafe child path scope: {raw!r}")
        normalized.append(str(path))
    return sorted(set(normalized))


def path_is_allowed(path: str, allowed_paths: list[str]) -> bool:
    return any(path == allowed or path.startswith(allowed.rstrip("/") + "/") for allowed in allowed_paths)


def child_fingerprint(parent_run_id: str, proposal: dict[str, Any]) -> str:
    payload = json.dumps(proposal, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(f"{parent_run_id}\0{payload}".encode("utf-8")).hexdigest()


def normalized_child_proposals(
    *,
    state: dict[str, Any],
    role: str,
    proposals: Any,
) -> list[dict[str, Any]]:
    if role not in SPAWNING_ROLES:
        raise TaskGraphError(f"role {role!r} is not allowed to propose child work")
    if not isinstance(proposals, list):
        raise TaskGraphError("child_tasks must be a list")
    depth = int(state.get("graph_depth", 0) or 0)
    if depth >= MAX_GRAPH_DEPTH:
        raise TaskGraphError("child task graph depth is exhausted")
    existing = state.get("children", [])
    existing_count = len(existing) if isinstance(existing, list) else 0
    if existing_count + len(proposals) > MAX_CHILDREN:
        raise TaskGraphError(f"a run may own at most {MAX_CHILDREN} child tasks")
    parent_repository = Path(str(state.get("repository", ""))).resolve()
    allowed_repositories = {
        str(Path(item).resolve())
        for item in state.get("allowed_child_repositories", [str(parent_repository)])
        if isinstance(item, str)
    }
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(proposals):
        if not isinstance(raw, dict):
            raise TaskGraphError(f"child_tasks[{index}] must be an object")
        unknown = sorted(
            set(raw)
            - {
                "task_id", "goal", "repository", "relation", "dependency_mode",
                "spawn_reason", "allowed_paths", "max_tokens", "max_duration_seconds",
            }
        )
        if unknown:
            raise TaskGraphError(
                f"child_tasks[{index}] has unexpected fields: {', '.join(unknown)}"
            )
        goal = str(raw.get("goal", "")).strip()
        if not goal or len(goal) > 4_000:
            raise TaskGraphError(f"child_tasks[{index}].goal must contain 1-4000 characters")
        repository = Path(str(raw.get("repository") or parent_repository)).expanduser().resolve()
        if str(repository) not in allowed_repositories or repository != parent_repository:
            raise TaskGraphError(
                "automatic child writers are currently restricted to the parent repository; "
                "use batch intake for cross-repository parallel work"
            )
        relation = str(raw.get("relation", "repair"))
        dependency_mode = str(raw.get("dependency_mode", "blocking"))
        if relation not in ALLOWED_RELATIONS or dependency_mode not in ALLOWED_DEPENDENCIES:
            raise TaskGraphError(f"child_tasks[{index}] relation or dependency_mode is invalid")
        allowed_paths = safe_allowed_paths(raw.get("allowed_paths", []))
        max_tokens = min(max(1_000, int(raw.get("max_tokens", MAX_CHILD_TOKENS))), MAX_CHILD_TOKENS)
        max_duration = min(
            max(60, int(raw.get("max_duration_seconds", MAX_CHILD_DURATION_SECONDS))),
            MAX_CHILD_DURATION_SECONDS,
        )
        task_seed = str(raw.get("task_id", "")).strip() or goal
        task_slug = "".join(character if character.isalnum() else "-" for character in task_seed.lower())
        task_slug = task_slug.strip("-")[:48] or f"child-{index + 1}"
        proposal = {
            "task_id": task_slug,
            "goal": goal,
            "repository": str(repository),
            "relation": relation,
            "dependency_mode": dependency_mode,
            "spawn_reason": str(raw.get("spawn_reason", goal)).strip()[:1000],
            "allowed_paths": allowed_paths,
            "child_budget": {
                "max_tokens": max_tokens,
                "max_duration_seconds": max_duration,
            },
        }
        proposal["spawn_fingerprint"] = child_fingerprint(str(state["run_id"]), proposal)
        normalized.append(proposal)
    return normalized


def spawn_children(
    *,
    queue: TaskQueue,
    state: dict[str, Any],
    role: str,
    proposals: Any,
) -> list[dict[str, Any]]:
    normalized = normalized_child_proposals(state=state, role=role, proposals=proposals)
    children = state.get("children", [])
    if not isinstance(children, list):
        children = []
    existing_fingerprints = {
        str(item.get("spawn_fingerprint", ""))
        for item in children
        if isinstance(item, dict)
    }
    spawned: list[dict[str, Any]] = []
    parent_run_id = str(state["run_id"])
    root_run_id = str(state.get("root_run_id") or parent_run_id)
    parent_branch = str(state.get("task_branch", state.get("branch", "task")))
    for proposal in normalized:
        fingerprint = str(proposal["spawn_fingerprint"])
        if fingerprint in existing_fingerprints:
            continue
        suffix = fingerprint.removeprefix("sha256:")[:10]
        child_run_id = f"{parent_run_id}-child-{suffix}"
        child_task_id = f"{proposal['task_id']}-{suffix[:6]}"
        branch = f"{parent_branch[:160]}-child-{proposal['task_id'][:32]}-{suffix[:6]}"
        payload = {
            "external_id": child_run_id,
            "task_key": f"child:{parent_run_id}:{fingerprint}",
            "task_id": child_task_id,
            "goal": proposal["goal"],
            "branch": branch,
            "base_branch": str(state.get("base_branch", "main")),
            "workspace_mode": "worktree",
            "mode": "fast",
            "priority": int(state.get("priority", 0) or 0) + 1,
            "max_retries": 1,
            "repository_max_parallel_tasks": int(
                state.get("repository_max_parallel_tasks", MAX_CHILDREN) or MAX_CHILDREN
            ),
            "run_id": child_run_id,
            "root_run_id": root_run_id,
            "parent_run_id": parent_run_id,
            "relation": proposal["relation"],
            "dependency_mode": proposal["dependency_mode"],
            "spawn_reason": proposal["spawn_reason"],
            "allowed_paths": proposal["allowed_paths"],
            "allowed_child_repositories": [proposal["repository"]],
            "graph_depth": int(state.get("graph_depth", 0) or 0) + 1,
            "child_budget": proposal["child_budget"],
            "spawn_fingerprint": fingerprint,
            "runtime_provider": str(
                state.get("runtime", {}).get("provider", "codex-sdk")
                if isinstance(state.get("runtime"), dict)
                else "codex-sdk"
            ),
        }
        envelope = normalize_event(
            source="api",
            payload=payload,
            repository=Path(proposal["repository"]),
            project=str(state.get("project", "agent_workspace")),
            project_id=str(state.get("project_id", "")),
            project_key=str(state.get("project_key", "")),
        )
        record = enqueue_envelope(queue, envelope)
        child = {
            "run_id": child_run_id,
            "queue_task_id": record.id,
            "task_id": child_task_id,
            "repository": proposal["repository"],
            "branch": branch,
            "relation": proposal["relation"],
            "dependency_mode": proposal["dependency_mode"],
            "spawn_reason": proposal["spawn_reason"],
            "allowed_paths": proposal["allowed_paths"],
            "spawn_fingerprint": fingerprint,
            "status": record.status,
            "join_status": "pending",
            "created_at": utc_now(),
        }
        children.append(child)
        spawned.append(child)
        existing_fingerprints.add(fingerprint)
    state["children"] = children
    return spawned


def git_changed_paths(repository: Path) -> list[str]:
    paths: set[str] = set()
    for command in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        result = subprocess.run(
            command,
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode == 0:
            paths.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(paths)


def finalize_child_run(run_dir: Path) -> tuple[bool, str]:
    """Create one verified binary patch without committing or publishing the child."""

    workflow_path = run_dir / "workflow.json"
    state = read_json(workflow_path)
    if not state.get("parent_run_id"):
        return True, ""
    checkout = Path(str(state.get("checkout_path", ""))).resolve()
    if not checkout.is_dir():
        return False, "child checkout is missing"
    allowed_paths = safe_allowed_paths(state.get("allowed_paths", []))
    changed = git_changed_paths(checkout)
    outside = [path for path in changed if not path_is_allowed(path, allowed_paths)]
    if outside:
        message = "child changed files outside its allowed scope: " + ", ".join(outside)
        state["execution_status"] = "blocked"
        state["blockers"] = [message]
        atomic_write_json(workflow_path, state)
        return False, message
    if changed:
        staged = subprocess.run(
            ["git", "add", "--", *changed],
            cwd=checkout,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if staged.returncode != 0:
            return False, staged.stderr.strip() or "child changes could not be staged for join"
    patch = subprocess.run(
        ["git", "diff", "--cached", "--binary", "HEAD"],
        cwd=checkout,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if patch.returncode != 0:
        return False, patch.stderr.decode("utf-8", errors="replace").strip() or "child patch failed"
    patch_path = run_dir / "artifacts" / "child.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_bytes(patch.stdout)
    result = {
        "status": "completed",
        "run_id": run_dir.name,
        "parent_run_id": str(state.get("parent_run_id", "")),
        "repository": str(state.get("repository", "")),
        "changed_files": changed,
        "patch_path": str(patch_path),
        "patch_sha256": hashlib.sha256(patch.stdout).hexdigest(),
        "tests_passed": read_json(run_dir / "artifacts" / "quality.json").get("verdict")
        not in {"broken", "fail"},
        "completed_at": utc_now(),
    }
    atomic_write_json(run_dir / "artifacts" / "child_result.json", result)
    state["child_result"] = result
    atomic_write_json(workflow_path, state)
    return True, ""


def _apply_child_patch(
    *, parent_checkout: Path, child_result: dict[str, Any]
) -> tuple[str, str]:
    patch_path = Path(str(child_result.get("patch_path", ""))).resolve()
    if not patch_path.is_file():
        return "failed", "child patch artifact is missing"
    if patch_path.stat().st_size == 0:
        return "joined", "child completed without repository changes"
    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=parent_checkout,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if check.returncode != 0:
        return "conflict", check.stderr.strip() or "child patch conflicts with parent changes"
    applied = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=parent_checkout,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if applied.returncode != 0:
        return "failed", applied.stderr.strip() or "child patch could not be applied"
    return "joined", "child patch applied"


def join_parent_children(
    *,
    queue: TaskQueue,
    state: dict[str, Any],
    runs_dir: Path = RUNS_DIR,
) -> tuple[bool, list[str]]:
    """Refresh children and consume each completed result at most once."""

    children = state.get("children", [])
    if not isinstance(children, list) or not children:
        return True, []
    wait_ids = set(state.get("wait_for_children", []))
    if not wait_ids:
        wait_ids = {
            str(child.get("run_id", ""))
            for child in children
            if isinstance(child, dict) and child.get("dependency_mode") == "blocking"
        }
    records = {record.run_id: record for record in queue.list() if record.run_id}
    pending: list[str] = []
    problems: list[str] = []
    parent_checkout = Path(str(state.get("checkout_path", ""))).resolve()
    for child in children:
        if not isinstance(child, dict):
            continue
        child_run_id = str(child.get("run_id", ""))
        record = records.get(child_run_id)
        if record is not None:
            child["status"] = record.status
        status = str(child.get("status", "queued"))
        if child_run_id in wait_ids and status not in {
            "completed", "blocked", "dead_letter", "failed", "cancelled"
        }:
            pending.append(child_run_id)
            continue
        if status != "completed":
            if child_run_id in wait_ids:
                child["join_status"] = "failed"
                problems.append(f"child {child_run_id} ended with {status}")
            continue
        if child.get("join_status") == "joined":
            continue
        child_result = read_json(runs_dir / child_run_id / "artifacts" / "child_result.json")
        if not child_result:
            pending.append(child_run_id)
            continue
        join_status, detail = _apply_child_patch(
            parent_checkout=parent_checkout,
            child_result=child_result,
        )
        child["join_status"] = join_status
        child["join_detail"] = detail[:1000]
        child["result"] = child_result
        child["joined_at"] = utc_now()
        if join_status != "joined":
            problems.append(f"child {child_run_id}: {detail}")
    state["children"] = children
    state["child_join_problems"] = problems
    return not pending, problems


def reconcile_waiting_parent(
    *,
    queue: TaskQueue,
    parent_run_id: str,
    runs_dir: Path = RUNS_DIR,
) -> None:
    parent = queue.find_run(parent_run_id)
    if parent is None or parent.status != "waiting_children":
        return
    run_dir = runs_dir / parent_run_id
    with graph_lock(run_dir):
        state = read_json(run_dir / "workflow.json")
        ready, problems = join_parent_children(queue=queue, state=state, runs_dir=runs_dir)
        if not ready:
            atomic_write_json(run_dir / "workflow.json", state)
            return
        state["execution_status"] = "resuming"
        state["resume_role"] = (
            "implementation-agent"
            if problems
            else str(state.get("resume_after_children", "implementation-agent"))
        )
        state["children_joined_at"] = utc_now()
        atomic_write_json(run_dir / "workflow.json", state)
        queue.transition_waiting_run(parent_run_id, status="resuming")
