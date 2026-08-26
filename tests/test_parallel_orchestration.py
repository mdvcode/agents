from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_harness.branch_conflicts import analyze_branch_conflicts
from ai_harness.task_batch import parse_batch_manifest
from ai_harness.workspace_cache import cache_environment
from task_graph import finalize_child_run, reconcile_waiting_parent, spawn_children
from task_queue import TaskQueue
from worker_pool import WorkerOutcome, WorkflowWorkerPool


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def initialize_repository(repository: Path) -> None:
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "tests@example.com")
    git(repository, "config", "user.name", "Harness Tests")
    (repository / "shared.txt").write_text("base\n", encoding="utf-8")
    git(repository, "add", "shared.txt")
    git(repository, "commit", "-m", "initial")


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_batch_manifest_resolves_parallel_worktrees_and_repository_limits(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    tasks = parse_batch_manifest(
        f"""
version: 1
repositories:
  backend:
    path: {backend}
    max_parallel_tasks: 3
  frontend:
    path: {frontend}
    max_parallel_tasks: 2
tasks:
  - repo: backend
    goal: Fix export
  - repo: backend
    goal: Add filters
    parallel: true
  - repo: frontend
    goal: Fix menu
""",
        base_dir=tmp_path,
    )

    assert [task["parallel"] for task in tasks] == [False, True, False]
    assert [task["max_parallel_tasks"] for task in tasks] == [3, 3, 2]
    assert [task["repository"] for task in tasks] == [backend, backend, frontend]


def test_repository_parallel_limit_is_enforced_inside_atomic_claim(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    shared = str((tmp_path / "shared").resolve())
    other = str((tmp_path / "other").resolve())
    first = queue.enqueue(
        task_key="shared-1",
        payload={
            "task_id": "shared-1",
            "repository": shared,
            "workspace_mode": "worktree",
            "repository_max_parallel_tasks": 1,
        },
    )
    second = queue.enqueue(
        task_key="shared-2",
        payload={
            "task_id": "shared-2",
            "repository": shared,
            "workspace_mode": "worktree",
            "repository_max_parallel_tasks": 1,
        },
    )
    unrelated = queue.enqueue(
        task_key="other",
        payload={
            "task_id": "other",
            "repository": other,
            "workspace_mode": "worktree",
            "repository_max_parallel_tasks": 1,
        },
    )

    assert queue.claim(worker_id="one").id == first.id  # type: ignore[union-attr]
    assert queue.claim(worker_id="two").id == unrelated.id  # type: ignore[union-attr]
    assert queue.claim(worker_id="three") is None
    queue.finish(task_id=first.id, worker_id="one", status="completed")
    assert queue.claim(worker_id="three").id == second.id  # type: ignore[union-attr]


def test_worktree_cache_environment_is_shared_per_repository(tmp_path: Path) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    first = cache_environment(tmp_path / "caches", repository)
    second = cache_environment(tmp_path / "caches", repository)

    assert first == second
    assert set(first) >= {
        "PIP_CACHE_DIR", "UV_CACHE_DIR", "npm_config_cache", "BUN_INSTALL_CACHE_DIR",
        "AGENT_BUILD_CACHE_DIR", "AGENT_VENV_CACHE_DIR", "AGENT_CONTAINER_CACHE_DIR",
    }
    assert all(Path(path).is_dir() for path in first.values())
    assert all(os.stat(path).st_mode & 0o777 == 0o700 for path in first.values())


def test_conflict_analysis_recommends_oldest_branch_first(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository)
    left = tmp_path / "left"
    right = tmp_path / "right"
    git(repository, "worktree", "add", "-b", "fix/left", str(left), "HEAD")
    git(repository, "worktree", "add", "-b", "fix/right", str(right), "HEAD")
    (left / "shared.txt").write_text("left\n", encoding="utf-8")
    (right / "shared.txt").write_text("right\n", encoding="utf-8")
    queue_items = [
        {"id": 1, "run_id": "left", "status": "running", "payload": {"task_id": "left", "repository": str(repository), "branch": "fix/left"}},
        {"id": 2, "run_id": "right", "status": "running", "payload": {"task_id": "right", "repository": str(repository), "branch": "fix/right"}},
    ]
    runs = {
        "left": {"checkout_path": str(left)},
        "right": {"checkout_path": str(right)},
    }

    conflicts = analyze_branch_conflicts(queue_items, runs)

    assert conflicts[0]["overlapping_paths"] == ["shared.txt"]
    assert conflicts[0]["recommended_first_run_id"] == "left"
    assert conflicts[0]["recommended_rebase_run_id"] == "right"


def test_conflict_analysis_deduplicates_resumed_queue_records(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository)
    left = tmp_path / "left"
    right = tmp_path / "right"
    git(repository, "worktree", "add", "-b", "fix/left", str(left), "HEAD")
    git(repository, "worktree", "add", "-b", "fix/right", str(right), "HEAD")
    (left / "shared.txt").write_text("left\n", encoding="utf-8")
    (right / "shared.txt").write_text("right\n", encoding="utf-8")
    queue_items = [
        {"id": 1, "run_id": "left", "status": "running", "payload": {"task_id": "left", "repository": str(repository), "branch": "fix/left"}},
        {"id": 3, "run_id": "left", "status": "running", "payload": {"task_id": "left", "repository": str(repository), "branch": "fix/left"}},
        {"id": 2, "run_id": "right", "status": "running", "payload": {"task_id": "right", "repository": str(repository), "branch": "fix/right"}},
        {"id": 4, "run_id": "right", "status": "running", "payload": {"task_id": "right", "repository": str(repository), "branch": "fix/right"}},
    ]
    runs = {
        "left": {"checkout_path": str(left)},
        "right": {"checkout_path": str(right)},
    }

    conflicts = analyze_branch_conflicts(queue_items, runs)

    assert len(conflicts) == 1
    assert conflicts[0]["recommended_first_run_id"] == "left"
    assert conflicts[0]["recommended_rebase_run_id"] == "right"


def test_completed_child_patch_is_joined_and_parent_resumes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository)
    child_checkout = tmp_path / "child-checkout"
    git(repository, "worktree", "add", "-b", "fix/child", str(child_checkout), "HEAD")
    (child_checkout / "shared.txt").write_text("child repair\n", encoding="utf-8")

    runs = tmp_path / "runs"
    parent_run = runs / "parent"
    child_run = runs / "child"
    write_json(
        child_run / "workflow.json",
        {
            "run_id": "child",
            "parent_run_id": "parent",
            "repository": str(repository),
            "checkout_path": str(child_checkout),
            "allowed_paths": ["shared.txt"],
            "execution_status": "completed",
        },
    )
    finalized, error = finalize_child_run(child_run)
    assert finalized, error

    write_json(
        parent_run / "workflow.json",
        {
            "run_id": "parent",
            "repository": str(repository),
            "checkout_path": str(repository),
            "execution_status": "waiting_children",
            "resume_after_children": "quality-runner",
            "wait_for_children": ["child"],
            "children": [
                {
                    "run_id": "child",
                    "dependency_mode": "blocking",
                    "status": "running",
                    "join_status": "pending",
                }
            ],
        },
    )
    queue = TaskQueue(tmp_path / "queue.db")
    parent = queue.enqueue(
        task_key="parent",
        payload={"task_id": "parent", "repository": str(repository)},
        run_id="parent",
    )
    child = queue.enqueue(
        task_key="child",
        payload={"task_id": "child", "repository": str(repository), "parent_run_id": "parent"},
        run_id="child",
    )
    assert queue.claim(worker_id="parent-worker").id == parent.id  # type: ignore[union-attr]
    queue.finish(
        task_id=parent.id,
        worker_id="parent-worker",
        status="waiting_children",
        run_id="parent",
    )
    assert queue.claim(worker_id="child-worker").id == child.id  # type: ignore[union-attr]
    queue.finish(task_id=child.id, worker_id="child-worker", status="completed", run_id="child")

    reconcile_waiting_parent(queue=queue, parent_run_id="parent", runs_dir=runs)

    state = json.loads((parent_run / "workflow.json").read_text(encoding="utf-8"))
    assert state["execution_status"] == "resuming"
    assert state["resume_role"] == "quality-runner"
    assert state["children"][0]["join_status"] == "joined"
    assert queue.find_run("parent").status == "resuming"  # type: ignore[union-attr]
    assert (repository / "shared.txt").read_text(encoding="utf-8") == "child repair\n"


def test_child_spawn_is_bounded_idempotent_and_uses_isolated_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    queue = TaskQueue(tmp_path / "queue.db")
    state: dict[str, object] = {
        "run_id": "parent",
        "root_run_id": "parent",
        "task_branch": "fix/parent",
        "base_branch": "main",
        "project": "agent_workspace",
        "repository": str(repository),
        "allowed_child_repositories": [str(repository)],
        "repository_max_parallel_tasks": 2,
        "graph_depth": 0,
        "children": [],
        "runtime": {"provider": "codex-sdk"},
    }
    proposal = {
        "task_id": "repair-parser",
        "goal": "Repair the parser failure",
        "repository": str(repository),
        "relation": "repair",
        "dependency_mode": "blocking",
        "spawn_reason": "Parser regression failed",
        "allowed_paths": ["reports/parser.py", "reports/tests"],
        "max_tokens": 20_000,
        "max_duration_seconds": 600,
    }

    first = spawn_children(
        queue=queue,
        state=state,
        role="implementation-agent",
        proposals=[proposal],
    )
    duplicate = spawn_children(
        queue=queue,
        state=state,
        role="implementation-agent",
        proposals=[proposal],
    )

    assert len(first) == 1
    assert duplicate == []
    record = queue.list()[0]
    assert record.payload["workspace_mode"] == "worktree"
    assert record.payload["parent_run_id"] == "parent"
    assert record.payload["dependency_mode"] == "blocking"
    assert record.payload["repository_max_parallel_tasks"] == 2


def test_dynamic_worker_slot_executes_child_while_parent_is_running(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    repository = str((tmp_path / "repository").resolve())
    child_completed = threading.Event()
    parent = queue.enqueue(
        task_key="parent",
        payload={"task_id": "parent", "repository": repository},
        run_id="parent",
    )

    def handler(record: object, worker_id: str) -> WorkerOutcome:
        del worker_id
        payload = record.payload  # type: ignore[attr-defined]
        if payload["task_id"] == "parent":
            queue.enqueue(
                task_key="child",
                payload={
                    "task_id": "child",
                    "repository": repository,
                    "parent_run_id": "parent",
                },
                run_id="child",
            )
            assert child_completed.wait(timeout=5), "idle worker did not pick up spawned child"
            return WorkerOutcome(status="completed", run_id="parent")
        child_completed.set()
        return WorkerOutcome(status="completed", run_id="child")

    pool = WorkflowWorkerPool(
        queue=queue,
        workers=2,
        lease_seconds=10,
        heartbeat_seconds=1,
        handler=handler,
        follow_dynamic_tasks=True,
    )
    records = pool.run_wave()

    assert queue.get(parent.id).status == "completed"
    assert queue.find_run("child").status == "completed"  # type: ignore[union-attr]
    assert {record.run_id for record in records} == {"parent", "child"}


def test_child_finalization_rejects_changes_outside_allowed_paths(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository)
    checkout = tmp_path / "child-checkout"
    git(repository, "worktree", "add", "-b", "fix/scoped-child", str(checkout), "HEAD")
    (checkout / "outside.txt").write_text("not allowed\n", encoding="utf-8")
    run_dir = tmp_path / "runs" / "child"
    write_json(
        run_dir / "workflow.json",
        {
            "run_id": "child",
            "parent_run_id": "parent",
            "repository": str(repository),
            "checkout_path": str(checkout),
            "allowed_paths": ["shared.txt"],
            "execution_status": "completed",
        },
    )

    finalized, error = finalize_child_run(run_dir)

    assert finalized is False
    assert "outside.txt" in error
    state = json.loads((run_dir / "workflow.json").read_text(encoding="utf-8"))
    assert state["execution_status"] == "blocked"
