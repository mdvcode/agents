from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

from ai_harness.project import trust_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ci_feedback import CIIngestionError, ingest_ci_failure
from task_queue import TaskQueue


def ci_fixture(tmp_path: Path) -> tuple[Path, bytes]:
    run = tmp_path / ".agent-runs" / "run-ci"
    (run / "artifacts").mkdir(parents=True)
    (run / "workflow.json").write_text(
        json.dumps(
            {
                "run_id": "run-ci",
                "task_id": "ci-task",
                "project": "agent_workspace",
                "project_id": "ci-project",
                "project_key": trust_key(tmp_path),
                "repository": str(tmp_path),
                "branch": "codex/ci-task",
                "base_branch": "main",
                "execution_status": "completed",
            }
        ),
        encoding="utf-8",
    )
    (run / "artifacts" / "publication.json").write_text(
        json.dumps(
            {
                "branch": "codex/ci-task",
                "pr_created_or_updated": True,
                "pr_url": "https://github.com/example/repo/pull/1",
            }
        ),
        encoding="utf-8",
    )
    body = json.dumps(
        {
            "agent_run_id": "run-ci",
            "workflow_run": {
                "id": 987,
                "status": "completed",
                "conclusion": "failure",
                "head_branch": "codex/ci-task",
                "head_sha": "abc",
                "html_url": "https://github.com/example/repo/actions/runs/987",
            },
        }
    ).encode("utf-8")
    return run, body


def test_signed_ci_failure_queues_repair_for_existing_run(tmp_path: Path) -> None:
    run, body = ci_fixture(tmp_path)
    signing_key = "web" + "hook-key"
    signature = "sha256=" + hmac.new(signing_key.encode(), body, hashlib.sha256).hexdigest()
    queue = TaskQueue(tmp_path / "queue.db")

    feedback, record = ingest_ci_failure(
        body=body,
        signature=signature,
        secret=signing_key,
        queue=queue,
        runs_dir=tmp_path / ".agent-runs",
        command_runner=lambda _args: (0, "failed test", ""),
    )

    assert feedback["run_id"] == "run-ci"
    assert record.run_id == "run-ci"
    assert record.payload["project_id"] == "ci-project"
    assert record.payload["project_key"] == trust_key(tmp_path)


def test_ci_feedback_redacts_logs_and_resumes_ci_repair(tmp_path: Path) -> None:
    run, body = ci_fixture(tmp_path)
    workflow_path = run / "workflow.json"
    workflow_before = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow_before.update(
        {
            "input_manifest": str(run / "inputs" / "manifest.json"),
            "input_manifest_sha256": "c" * 64,
            "attachment_count": 3,
            "attachment_runtime_consent": True,
        }
    )
    workflow_path.write_text(json.dumps(workflow_before), encoding="utf-8")
    signing_key = "web" + "hook-key"
    signature = "sha256=" + hmac.new(signing_key.encode(), body, hashlib.sha256).hexdigest()
    queue = TaskQueue(tmp_path / "queue.db")
    leaked = "gh" + "p_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"

    feedback, record = ingest_ci_failure(
        body=body,
        signature=signature,
        secret=signing_key,
        queue=queue,
        runs_dir=tmp_path / ".agent-runs",
        command_runner=lambda args: (0, f"failure {leaked}\n", ""),
    )

    workflow = json.loads((run / "workflow.json").read_text(encoding="utf-8"))
    logs = (run / feedback["logs_path"]).read_text(encoding="utf-8")
    assert leaked not in logs
    assert "[REDACTED]" in logs
    assert workflow["execution_status"] == "resuming"
    assert workflow["resume_role"] == "ci-repair-agent"
    assert record.payload["branch"] == "codex/ci-task"
    assert record.payload["input_manifest"] == str(run / "inputs" / "manifest.json")
    assert record.payload["input_manifest_sha256"] == "c" * 64
    assert record.payload["attachment_count"] == 3
    assert record.payload["attachment_runtime_consent"] is True
    assert feedback["existing_pr_url"].endswith("/pull/1")


def test_ci_webhook_rejects_invalid_signature(tmp_path: Path) -> None:
    _run, body = ci_fixture(tmp_path)
    with pytest.raises(CIIngestionError, match="signature"):
        ingest_ci_failure(
            body=body,
            signature="sha256=bad",
            secret="web" + "hook-key",
            queue=TaskQueue(tmp_path / "queue.db"),
            runs_dir=tmp_path / ".agent-runs",
        )
