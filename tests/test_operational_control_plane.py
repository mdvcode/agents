from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from approval_lifecycle import request_approval
from control_plane_api import handler_factory
from operational_metrics import collect_metrics
from task_queue import TaskQueue


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def awaiting_run(runs: Path, repository: Path) -> Path:
    run = runs / "api-run"
    write_json(
        run / "workflow.json",
        {
            "run_id": "api-run",
            "workflow": "full_agent_workflow",
            "task_id": "api-task",
            "goal": "resume through API",
            "project": "agent_workspace",
            "repository": str(repository),
            "worktree": str(repository),
            "branch": "codex/api-task",
            "base_branch": "main",
            "execution_status": "awaiting_approval",
            "input_fingerprint": "fingerprint",
            "role_count": 2,
            "tokens_used": 10,
            "elapsed_seconds": 1,
            "risk_class": "high",
            "budgets": {
                "max_roles": 40,
                "max_tokens": 300000,
                "max_duration_seconds": 7200,
                "max_repair_iterations": 12,
            },
            "roles": [
                {"role": "risk-classifier", "result": {"status": "completed"}},
                {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
            ],
        },
    )
    request_approval(run, reason="HIGH risk")
    return run


def api_request(url: str, token: str, *, method: str = "GET", body: dict[str, object] | None = None) -> dict[str, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        value = json.loads(response.read())
    assert isinstance(value, dict)
    return value


def test_metrics_cover_runs_workers_queue_leases_budgets_and_exceptions(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    awaiting_run(runs, tmp_path)
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue(task_key="queued", payload={"task_id": "q", "repository": str(tmp_path)})
    queue.register_worker(worker_id="svc-1", service_id="svc", pid=123)

    metrics = collect_metrics(runs_dir=runs, db_path=queue.path)

    assert set(metrics) >= {"runs", "workers", "queue", "leases", "budgets", "exceptions"}
    assert metrics["runs"]["counts"]["awaiting_approval"] == 1
    assert metrics["queue"]["counts"]["queued"] == 1
    assert metrics["budgets"]["tokens_used"] == 10
    assert any(item["run_id"] == "api-run" for item in metrics["exceptions"])


def test_control_plane_api_approves_resumes_and_accepts_tasks(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    awaiting_run(runs, tmp_path)
    queue = TaskQueue(tmp_path / "queue.db")
    access_key = "test-control-" + "key"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=queue,
            runs_dir=runs,
            auth_token=access_key,
            webhook_secret="web" + "hook-key",
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        try:
            urlopen(f"{base}/metrics", timeout=5)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("metrics API must require its configured token")

        approved = api_request(
            f"{base}/runs/api-run/approve",
            access_key,
            method="POST",
            body={"actor": "human-reviewer", "reason": "scope reviewed"},
        )
        resumed = api_request(f"{base}/runs/api-run/resume", access_key, method="POST", body={})
        task = api_request(
            f"{base}/tasks",
            access_key,
            method="POST",
            body={
                "source": "api",
                "repository": str(tmp_path),
                "payload": {"external_id": "api-1", "task_id": "task-1", "goal": "API intake"},
            },
        )
        metrics = api_request(f"{base}/metrics", access_key)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert approved["status"] == "approved"
    assert resumed["workflow"]["execution_status"] == "resuming"
    assert resumed["queue_task"]["run_id"] == "api-run"
    assert task["envelope"]["source"] == "api"
    assert metrics["queue"]["counts"]["queued"] == 2
