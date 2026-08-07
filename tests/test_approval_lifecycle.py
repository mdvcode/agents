from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from approval_lifecycle import (  # noqa: E402
    ApprovalError,
    approve_run,
    expire_if_needed,
    prepare_resume,
    reject_run,
    request_approval,
    resume_run,
)
from task_queue import TaskQueue


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def awaiting_run(tmp_path: Path) -> Path:
    run = tmp_path / ".agent-runs" / "run-approval"
    write_json(
        run / "workflow.json",
        {
            "run_id": "run-approval",
            "execution_status": "awaiting_approval",
            "input_fingerprint": "task-fingerprint",
            "worktree": str(tmp_path / "worktree"),
            "branch": "feat/task",
            "role_count": 2,
            "tokens_used": 10,
            "roles": [
                {"role": "risk-classifier", "result": {"status": "completed"}},
                {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
            ],
        },
    )
    return run


def test_approval_scope_is_exact_and_consumed_once(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["attention"] = {
        "required": True,
        "summary": "Approval required.",
        "details": ["budget exceeded"],
        "role": "risk-classifier",
        "action": "approve",
    }
    workflow["blockers"] = ["budget exceeded"]
    write_json(workflow_path, workflow)
    requested = request_approval(run, reason="HIGH risk")
    expanded = {**requested["requested_scope"], "actions": ["resume_workflow", "merge"]}

    with pytest.raises(ApprovalError, match="exactly match"):
        approve_run(run, actor="reviewer", scope=expanded)

    approved = approve_run(run, actor="reviewer", reason="approved for local patch")
    resumed = prepare_resume(run)

    assert approved["status"] == "approved"
    assert resumed["approval"]["status"] == "consumed"
    assert resumed["workflow"]["execution_status"] == "resuming"
    assert resumed["workflow"]["resume_role"] == "risk-classifier"
    assert resumed["workflow"]["approval_override"]["gate"] == "risk-classifier"
    assert resumed["workflow"]["approval_grants"][0]["reason"] == "HIGH risk"
    assert "attention" not in resumed["workflow"]
    assert resumed["workflow"]["blockers"] == []
    assert resumed["workflow"]["attention_history"][-1]["resolution"] == "approval_consumed"
    replay = prepare_resume(run)
    assert replay["already_consumed"] is True
    assert len(replay["workflow"]["approval_grants"]) == 1


def test_rejection_blocks_workflow(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    request_approval(run, reason="review required")

    rejected = reject_run(run, actor="reviewer", reason="scope is unsafe")
    workflow = json.loads((run / "workflow.json").read_text(encoding="utf-8"))

    assert rejected["status"] == "rejected"
    assert workflow["execution_status"] == "blocked"
    assert workflow["blockers"] == ["approval rejected: scope is unsafe"]


def test_expired_approval_cannot_resume(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    approval = request_approval(run, reason="review required")
    approval["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    expired = expire_if_needed(run, approval)

    assert expired["status"] == "expired"
    assert expired["decision_reason"] == "approval expired"
    workflow = json.loads((run / "workflow.json").read_text(encoding="utf-8"))
    assert workflow["execution_status"] == "blocked"
    assert "APPROVAL_EXPIRED" in (run / "errors.jsonl").read_text(encoding="utf-8")


def test_resume_enqueues_same_run_and_checkpoint(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow.update(
        {
            "task_id": "approval-task",
            "goal": "continue safely",
            "project": "agent_workspace",
            "repository": str(tmp_path),
            "base_branch": "main",
        }
    )
    write_json(workflow_path, workflow)
    request_approval(run, reason="HIGH risk")
    approve_run(run, actor="reviewer")
    queue = TaskQueue(tmp_path / "queue.db")

    transition, record = resume_run(run, queue=queue)

    assert transition["workflow"]["execution_status"] == "resuming"
    assert record.run_id == "run-approval"
    assert record.payload["run_id"] == "run-approval"
    assert record.payload["repository"] == str(tmp_path)
    assert record.status == "queued"
    replay, replay_record = resume_run(run, queue=queue)
    assert replay["already_consumed"] is True
    assert replay_record.id == record.id


def test_concurrent_resume_consumes_and_enqueues_exactly_once(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    request_approval(run, reason="HIGH risk")
    approve_run(run, actor="reviewer")
    queue = TaskQueue(tmp_path / "queue.db")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _item: resume_run(run, queue=queue), range(4)))

    assert {record.id for _transition, record in results} == {results[0][1].id}
    workflow = json.loads((run / "workflow.json").read_text(encoding="utf-8"))
    approval = json.loads((run / "artifacts" / "approval.json").read_text(encoding="utf-8"))
    assert approval["resume_count"] == 1
    assert len(workflow["approval_grants"]) == 1


def test_high_risk_request_explicitly_scopes_patch_authority(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    artifacts = run / "artifacts"
    write_json(artifacts / "risk.json", {"risk_class": "high"})
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["artifacts_dir"] = str(artifacts)
    write_json(workflow_path, workflow)

    approval = request_approval(run, reason="HIGH risk")

    assert approval["requested_scope"]["actions"] == ["patch_high_risk", "resume_workflow"]
    assert approval["requested_scope"]["risk_class"] == "high"
