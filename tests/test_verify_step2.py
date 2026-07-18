from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue
from verify_step2 import verify


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_run(runs: Path, tmp_path: Path, run_id: str, *, published: bool) -> Path:
    run = runs / run_id
    worktree = tmp_path / "worktrees" / run_id
    worktree.mkdir(parents=True)
    write_json(
        run / "workflow.json",
        {
            "execution_status": "completed" if published else "awaiting_approval",
            "worktree": str(worktree),
            "loops": {
                "quality_repair": {},
                "review_repair": {},
                "ci_repair": {},
                "frontend_verification_repair": {},
            },
            "roles": [
                {"role": "security-agent", "result": {"status": "completed"}},
                {"role": "reviewer", "result": {"status": "completed"}},
            ],
            "executor": {"kind": "codex_cli", "production": True},
        },
    )
    write_json(run / "metrics.json", {"tokens_used": 10})
    write_json(run / "artifacts" / "issue.json", {"worktree": str(worktree)})
    raw = run / "raw-events"
    raw.mkdir(parents=True)
    (raw / "planner.jsonl").write_text('{"type":"thread.started"}\n', encoding="utf-8")
    (raw / "tool-calls.jsonl").write_text(
        json.dumps({"tool": "filesystem_read", "allowed": True}) + "\n",
        encoding="utf-8",
    )
    if published:
        write_json(
            run / "artifacts" / "publication.json",
            {
                "execution_status": "completed",
                "pr_created_or_updated": True,
                "branch_pushed": True,
                "pr_url": f"https://github.com/example/repo/pull/{run_id}",
                "worktree": str(worktree),
            },
        )
    return run


def test_three_concurrent_real_workflow_records_pass_step2_gate(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    queue = TaskQueue(tmp_path / "queue.db")
    tasks = [
        queue.enqueue(
            task_key=f"task-{index}",
            payload={"task_id": str(index), "repository": str(tmp_path)},
            run_id=f"run-{index}",
        )
        for index in range(3)
    ]
    first = queue.claim(worker_id="worker-1")
    second = queue.claim(worker_id="worker-2")
    assert first is not None and second is not None
    queue.mark_running(first.id, "worker-1")
    queue.mark_running(second.id, "worker-2")
    queue.finish(task_id=first.id, worker_id="worker-1", status="completed", run_id=first.run_id)
    queue.finish(task_id=second.id, worker_id="worker-2", status="completed", run_id=second.run_id)
    third = queue.claim(worker_id="worker-3")
    assert third is not None
    queue.mark_running(third.id, "worker-3")
    queue.finish(
        task_id=third.id,
        worker_id="worker-3",
        status="blocked",
        run_id=third.run_id,
        requires_human=True,
        exception_reason="approval required",
    )
    for index, _task in enumerate(tasks):
        make_run(runs, tmp_path, f"run-{index}", published=index < 2)

    result = verify(runs_dir=runs, db_path=queue.path)

    assert result["status"] == "pass", result["blockers"]
    assert result["max_concurrency"] == 2
    assert result["isolated_worktrees"] is True


def test_step2_gate_rejects_empty_queue(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")

    result = verify(runs_dir=tmp_path / ".agent-runs", db_path=queue.path)

    assert result["status"] == "fail"
    assert any("minimum is 3" in blocker for blocker in result["blockers"])
