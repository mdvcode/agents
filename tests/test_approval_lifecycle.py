from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import approval_lifecycle  # noqa: E402
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
from workflow_router import diff_hash, failure_fingerprint


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


def exhausted_reviewer_run(tmp_path: Path) -> Path:
    run = awaiting_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    result = {"status": "completed", "summary": "Concrete review blockers remain."}
    workflow["roles"] = [
        {"role": "reviewer", "result": result},
        {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
    ]
    artifacts_dir = run / "artifacts"
    workflow["artifacts_dir"] = str(artifacts_dir)
    write_json(
        artifacts_dir / "review.json",
        {
            "verdict": "broken",
            "status": "block",
            "blockers": ["A concrete code defect remains."],
            "blocker_ids": ["REV-001"],
        },
    )
    failure = failure_fingerprint(
        role_result=result,
        state=workflow,
        artifacts_dir=artifacts_dir,
    )
    current_diff = diff_hash(workflow, artifacts_dir)
    workflow["last_route"] = {
        "next_role": "approval-gate",
        "stop": True,
        "loop": {
            "name": "review_repair",
            "iteration": 3,
            "max_iterations": 3,
            "failure_fingerprint": failure,
            "diff_fingerprint": current_diff,
            "progress_detected": True,
        },
    }
    workflow["loops"] = {
        "review_repair": {
            "iterations": 3,
            "max_iterations": 3,
            "last_failure_fingerprint": failure,
            "last_diff_fingerprint": current_diff,
            "progress_detected": True,
            "extensions_used": 0,
        }
    }
    write_json(workflow_path, workflow)
    write_json(
        run / "checkpoints" / "reviewer.json",
        {
            "run_id": "run-approval",
            "role": "reviewer",
            "state": "role_completed",
            "attempt": 3,
            "worktree": str(tmp_path / "worktree"),
            "input_fingerprint": "task-fingerprint",
            "output_fingerprint": "sha256:review-result",
            "artifacts": ["review.json"],
            "side_effects": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return run


def test_approval_scope_is_exact_and_consumed_once(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    write_json(
        run / "checkpoints" / "risk-classifier.json",
        {
            "run_id": "run-approval",
            "role": "risk-classifier",
            "state": "role_validating",
            "attempt": 1,
            "worktree": str(worktree),
            "input_fingerprint": "task-fingerprint",
            "output_fingerprint": "sha256:old-result",
            "artifacts": ["risk.json"],
            "side_effects": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
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
    checkpoint = json.loads(
        (run / "checkpoints" / "risk-classifier.json").read_text(encoding="utf-8")
    )
    assert checkpoint["state"] == "role_pending"
    assert checkpoint["output_fingerprint"] == ""
    assert checkpoint["artifacts"] == []
    replay = prepare_resume(run)
    assert replay["already_consumed"] is True
    assert len(replay["workflow"]["approval_grants"]) == 1


def test_approval_wait_does_not_exhaust_recovery_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = awaiting_run(tmp_path)
    requested_at = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    resumed_at = requested_at + timedelta(hours=1)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["recovery"] = {
        "started_at": requested_at.timestamp() - 12,
        "elapsed_seconds": 12,
        "attempts": 1,
    }
    write_json(workflow_path, workflow)
    now = requested_at
    monkeypatch.setattr(approval_lifecycle, "utc_now", lambda: now)

    request_approval(run, reason="review required")
    approve_run(run, actor="reviewer")
    now = resumed_at
    resumed = prepare_resume(run)

    recovery = resumed["workflow"]["recovery"]
    assert recovery["started_at"] == resumed_at.timestamp() - 12
    assert recovery["elapsed_seconds"] == 12
    assert recovery["attempts"] == 1

    replay = prepare_resume(run)
    assert replay["workflow"]["recovery"] == recovery


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
    queue = TaskQueue(tmp_path / ".agent-queue" / "tasks.db")
    queued = queue.enqueue(
        task_key="approval-expiry",
        payload={"task_id": "approval-expiry", "repository": str(tmp_path)},
        run_id="run-approval",
    )
    claimed = queue.claim(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None and claimed.id == queued.id
    assert queue.mark_running(queued.id, "worker-1")
    queue.finish(
        task_id=queued.id,
        worker_id="worker-1",
        status="awaiting_approval",
        run_id="run-approval",
        requires_human=True,
        exception_reason="review required",
    )
    approval = request_approval(run, reason="review required")
    approval["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    expired = expire_if_needed(run, approval)

    assert expired["status"] == "expired"
    assert expired["decision_reason"] == "approval expired"
    workflow = json.loads((run / "workflow.json").read_text(encoding="utf-8"))
    assert workflow["execution_status"] == "blocked"
    assert "APPROVAL_EXPIRED" in (run / "errors.jsonl").read_text(encoding="utf-8")
    expired_task = queue.get(queued.id)
    assert expired_task is not None
    assert expired_task.status == "blocked"
    assert expired_task.exception_reason == "approval expired"
    recovered = queue.recover_run("run-approval", action="retry")
    assert recovered.status == "retry_wait"


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
            "workspace_mode": "checkout",
            "checkout_path": str(tmp_path),
            "task_branch": "feat/task",
            "base_sha": "abc123",
            "branch_owner_run_id": "run-approval",
            "runtime": {"provider": "codex-sdk"},
        }
    )
    write_json(workflow_path, workflow)
    request_approval(run, reason="HIGH risk")
    approve_run(run, actor="reviewer")
    queue = TaskQueue(tmp_path / "queue.db")
    predecessor = queue.enqueue(
        task_key="original-task",
        payload={"task_id": "approval-task", "repository": str(tmp_path)},
        run_id="run-approval",
    )
    claimed = queue.claim(worker_id="original-worker")
    assert claimed is not None and claimed.id == predecessor.id
    assert queue.mark_running(predecessor.id, "original-worker")
    queue.finish(
        task_id=predecessor.id,
        worker_id="original-worker",
        status="awaiting_approval",
        run_id="run-approval",
        requires_human=True,
        exception_reason="approval required",
    )

    transition, record = resume_run(run, queue=queue)

    assert transition["workflow"]["execution_status"] == "resuming"
    assert record.run_id == "run-approval"
    assert record.payload["run_id"] == "run-approval"
    assert record.payload["repository"] == str(tmp_path)
    assert record.payload["workspace_mode"] == "checkout"
    assert record.payload["checkout_path"] == str(tmp_path)
    assert record.payload["task_branch"] == "feat/task"
    assert record.payload["base_sha"] == "abc123"
    assert record.payload["branch_owner_run_id"] == "run-approval"
    assert record.payload["runtime_provider"] == "codex-sdk"
    assert record.status == "queued"
    superseded = queue.get(predecessor.id)
    assert superseded is not None and superseded.status == "completed"
    assert any(
        event["event"] == "superseded_by_resume"
        for event in queue.events(predecessor.id)
    )
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


def test_security_request_is_scoped_to_current_findings(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["roles"] = [
        {"role": "security-agent", "result": {"status": "completed"}},
        {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
    ]
    workflow["artifacts_dir"] = str(run / "artifacts")
    write_json(workflow_path, workflow)
    write_json(
        run / "artifacts" / "security.json",
        {
            "verdict": "broken",
            "status": "fail",
            "highest_severity": "medium",
            "blocker_ids": ["SEC-AUTH-001"],
            "findings": [
                {
                    "id": "SEC-AUTH-001",
                    "severity": "medium",
                    "status": "confirmed",
                    "category": "debug_logging",
                    "scope": "pre-existing",
                }
            ],
        },
    )

    approval = request_approval(run, reason="Security acceptance required")

    assert approval["requested_scope"]["actions"] == [
        "accept_security_finding",
        "resume_workflow",
    ]
    assert approval["requested_scope"]["finding_ids"] == ["SEC-AUTH-001"]
    assert len(approval["requested_scope"]["security_fingerprint"]) == 64


def test_verifier_request_is_scoped_to_current_artifact(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["roles"] = [
        {"role": "semantic-conflict-agent", "result": {"status": "completed"}},
        {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
    ]
    workflow["artifacts_dir"] = str(run / "artifacts")
    write_json(workflow_path, workflow)
    write_json(
        run / "artifacts" / "semantic_conflict.json",
        {
            "verdict": "broken",
            "blockers": ["Browser verification is unavailable."],
        },
    )

    approval = request_approval(run, reason="Verifier unavailable")

    assert approval["requested_scope"]["actions"] == [
        "accept_unavailable_verification",
        "resume_workflow",
    ]
    assert len(approval["requested_scope"]["verifier_fingerprint"]) == 64


def test_available_verifier_requests_one_time_repair_extension(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["roles"] = [
        {"role": "architecture-consistency-agent", "result": {"status": "completed"}},
        {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
    ]
    workflow["artifacts_dir"] = str(run / "artifacts")
    workflow["last_route"] = {
        "next_role": "approval-gate",
        "stop": True,
        "loop": {
            "name": "review_repair",
            "iteration": 3,
            "max_iterations": 3,
            "progress_detected": True,
            "failure_fingerprint": "a" * 64,
            "diff_fingerprint": "b" * 64,
        },
    }
    workflow["loops"] = {
        "review_repair": {
            "iterations": 3,
            "max_iterations": 3,
            "last_failure_fingerprint": "a" * 64,
            "last_diff_fingerprint": "b" * 64,
            "progress_detected": True,
            "extensions_used": 0,
        }
    }
    write_json(workflow_path, workflow)
    write_json(
        run / "artifacts" / "architecture_consistency.json",
        {
            "verdict": "broken",
            "blockers": ["Public archive queries bypass the publication policy."],
        },
    )

    approval = request_approval(run, reason="Repair budget exhausted")

    assert approval["requested_scope"]["actions"] == [
        "extend_review_repair_once",
        "resume_workflow",
    ]
    assert "accept_unavailable_verification" not in approval["requested_scope"]["actions"]
    assert approval["requested_scope"]["loop_name"] == "review_repair"
    assert approval["requested_scope"]["at_iteration"] == 3
    assert approval["requested_scope"]["max_iterations"] == 3
    assert approval["requested_scope"]["failure_fingerprint"] == "a" * 64
    assert approval["requested_scope"]["diff_fingerprint"] == "b" * 64
    assert approval["requested_scope"]["additional_attempts"] == 1
    assert len(approval["requested_scope"]["verifier_fingerprint"]) == 64


def test_available_verifier_without_exhausted_loop_cannot_be_accepted(
    tmp_path: Path,
) -> None:
    run = awaiting_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["roles"] = [
        {"role": "reviewer", "result": {"status": "completed"}},
        {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
    ]
    workflow["artifacts_dir"] = str(run / "artifacts")
    write_json(workflow_path, workflow)
    write_json(
        run / "artifacts" / "review.json",
        {"verdict": "broken", "blockers": ["A concrete code defect remains."]},
    )

    approval = request_approval(run, reason="Concrete verifier blocker")

    assert approval["requested_scope"]["actions"] == ["resume_workflow"]


def test_stale_or_inconsistent_review_loop_does_not_offer_extension(
    tmp_path: Path,
) -> None:
    run = exhausted_reviewer_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["loops"]["review_repair"]["last_diff_fingerprint"] = "c" * 64
    write_json(workflow_path, workflow)

    approval = request_approval(run, reason="Stale repair checkpoint")

    assert approval["requested_scope"]["actions"] == ["resume_workflow"]


def test_explicit_unbound_review_extension_scope_is_rejected(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)

    with pytest.raises(ApprovalError, match="exact exhausted verifier scope"):
        request_approval(
            run,
            reason="Unbound extension",
            scope={
                "actions": ["extend_review_repair_once", "resume_workflow"],
                "gate": "risk-classifier",
                "additional_attempts": 1,
            },
        )


@pytest.mark.parametrize("value", [True, "1", 1.9])
def test_extension_scope_integer_types_are_exact(
    tmp_path: Path,
    value: object,
) -> None:
    run = exhausted_reviewer_run(tmp_path)
    requested = request_approval(run, reason="Repair budget exhausted")
    expanded = dict(requested["requested_scope"])
    expanded["additional_attempts"] = value

    with pytest.raises(ApprovalError, match="must be an integer"):
        approve_run(run, actor="reviewer", scope=expanded)


def test_valid_review_extension_preserves_completed_verifier_checkpoint(
    tmp_path: Path,
) -> None:
    run = exhausted_reviewer_run(tmp_path)
    requested = request_approval(run, reason="Repair budget exhausted")
    approve_run(run, actor="reviewer", reason="One local repair only")

    resumed = prepare_resume(run)

    checkpoint = json.loads(
        (run / "checkpoints" / "reviewer.json").read_text(encoding="utf-8")
    )
    assert checkpoint["state"] == "role_completed"
    assert checkpoint["output_fingerprint"] == "sha256:review-result"
    assert checkpoint["artifacts"] == ["review.json"]
    assert resumed["workflow"]["resume_role"] == "reviewer"
    assert resumed["workflow"]["approval_override"]["scope"] == requested["requested_scope"]
    assert len(resumed["workflow"]["approval_grants"]) == 1


def test_invalid_verifier_checkpoint_cannot_suppress_reset_or_consume_extension(
    tmp_path: Path,
) -> None:
    run = exhausted_reviewer_run(tmp_path)
    checkpoint_path = run / "checkpoints" / "reviewer.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["state"] = "role_validating"
    write_json(checkpoint_path, checkpoint)
    request_approval(run, reason="Repair budget exhausted")
    approve_run(run, actor="reviewer", reason="One local repair only")

    with pytest.raises(ApprovalError, match="completed verifier checkpoint"):
        prepare_resume(run)

    stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    workflow = json.loads((run / "workflow.json").read_text(encoding="utf-8"))
    approval = json.loads((run / "artifacts" / "approval.json").read_text(encoding="utf-8"))
    assert stored["state"] == "role_validating"
    assert stored["output_fingerprint"] == "sha256:review-result"
    assert workflow["execution_status"] == "awaiting_approval"
    assert approval["status"] == "approved"


def test_works_with_browser_warning_is_not_unavailable(tmp_path: Path) -> None:
    run = awaiting_run(tmp_path)
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["roles"] = [
        {"role": "reviewer", "result": {"status": "completed"}},
        {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
    ]
    workflow["artifacts_dir"] = str(run / "artifacts")
    write_json(workflow_path, workflow)
    write_json(
        run / "artifacts" / "review.json",
        {
            "verdict": "works",
            "blockers": [],
            "warnings": ["Browser verification is unavailable."],
        },
    )

    approval = request_approval(run, reason="Review completed with a warning")

    assert approval["requested_scope"]["actions"] == ["resume_workflow"]
