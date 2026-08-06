from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from approval_lifecycle import request_approval
from control_plane_api import ControlPlaneHandler, handler_factory
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

    assert set(metrics) >= {
        "runs", "workers", "queue", "leases", "budgets", "exceptions",
        "overview", "latency", "costs", "retries", "loops", "failures", "tracing",
    }
    assert metrics["runs"]["counts"]["awaiting_approval"] == 1
    assert metrics["queue"]["counts"]["queued"] == 1
    assert metrics["budgets"]["tokens_used"] == 10
    assert any(item["run_id"] == "api-run" for item in metrics["exceptions"])


def test_metrics_derive_latency_cost_retries_loops_pr_time_and_failures(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    run = runs / "observed"
    started = datetime.fromtimestamp(time.time() - 120, tz=timezone.utc).isoformat()
    write_json(
        run / "workflow.json",
        {
            "task_id": "observed",
            "project": "agent_workspace",
            "execution_status": "completed",
            "started_at": started,
            "elapsed_seconds": 12,
            "tokens_used": 50,
            "loops": {"quality_repair": {"iterations": 2}},
        },
    )
    write_json(run / "metrics.json", {"duration_ms": 12_000, "cost_usd": 0.25})
    write_json(run / "artifacts" / "publication.json", {"pr_created_or_updated": True})
    (run / "errors.jsonl").write_text('{"code":"KNOWN_FAILURE"}\n', encoding="utf-8")
    runner = run / "raw-events" / "workflow-runner.jsonl"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(
        '\n'.join(
            [
                '{"event":"iteration_started","iteration":1}',
                '{"step":"quality","attempt":1,"returncode":1}',
                '{"step":"quality","attempt":2,"returncode":0}'
            ]
        ) + '\n',
        encoding="utf-8",
    )
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.enqueue(task_key="retry", payload={"task_id": "retry", "repository": str(tmp_path)}, max_retries=1)
    claimed = queue.claim(worker_id="worker", lease_seconds=30)
    assert claimed is not None
    queue.mark_running(task.id, "worker")
    queue.finish(task_id=task.id, worker_id="worker", status="failed", error="redacted")
    claimed = queue.claim(worker_id="worker", lease_seconds=30)
    assert claimed is not None
    queue.mark_running(task.id, "worker")
    queue.finish(task_id=task.id, worker_id="worker", status="completed")

    metrics = collect_metrics(runs_dir=runs, db_path=queue.path)

    assert metrics["costs"] == {"known_usd": 0.25, "known_runs": 1, "unknown_runs": 0, "coverage": 1.0}
    assert metrics["retries"]["total"] == 2
    assert metrics["loops"]["total_iterations"] == 3
    assert metrics["failures"]["run_failures"] == 1
    assert metrics["latency"]["pr_time_seconds"]["samples"] == 1
    assert metrics["latency"]["queue_wait_seconds"]["samples"] == 1


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

        with urlopen(f"{base}/dashboard", timeout=5) as response:
            dashboard = response.read().decode("utf-8")
            assert response.headers["Content-Security-Policy"]
        assert "Agent Control" in dashboard
        assert "Новая задача" in dashboard
        assert "api-run" not in dashboard

        config = api_request(f"{base}/config", access_key)
        assert config["default_repository"] == str(ROOT)

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
        traces = api_request(f"{base}/traces", access_key)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert approved["status"] == "approved"
    assert resumed["workflow"]["execution_status"] == "resuming"
    assert resumed["queue_task"]["run_id"] == "api-run"
    assert task["envelope"]["source"] == "api"
    assert metrics["queue"]["counts"]["queued"] == 2
    assert traces["count"] == 0


def test_dashboard_task_and_run_controls_delegate_to_product_cli(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    calls: list[tuple[Path, list[str]]] = []

    def command(
        _handler: object,
        repository: Path,
        arguments: list[str],
        *,
        timeout: int = 120,
    ) -> dict[str, object]:
        calls.append((repository, arguments))
        return {"status": "queued", "task_id": "kc-432", "timeout": timeout}

    monkeypatch.setattr(ControlPlaneHandler, "agent_command", command)
    queue = TaskQueue(tmp_path / "queue.db")
    token = "dashboard-test-token"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=queue,
            runs_dir=tmp_path / "runs",
            auth_token=token,
            webhook_secret="",
            default_repository=tmp_path,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        launched = api_request(
            f"{base}/ui/tasks",
            token,
            method="POST",
            body={"repository": str(tmp_path), "goal": "Implement KC-432", "mode": "new_branch"},
        )
        aborted = api_request(
            f"{base}/ui/runs/run-432/abort",
            token,
            method="POST",
            body={"repository": str(tmp_path)},
        )
        answered = api_request(
            f"{base}/ui/runs/run-432/answer",
            token,
            method="POST",
            body={"repository": str(tmp_path), "response": "Use the existing allowlist."},
        )
        approved = api_request(
            f"{base}/ui/runs/run-432/approve",
            token,
            method="POST",
            body={"repository": str(tmp_path), "reason": "Reviewed locally."},
        )
        retried = api_request(
            f"{base}/ui/runs/run-432/retry",
            token,
            method="POST",
            body={"repository": str(tmp_path)},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert launched["task_id"] == "kc-432"
    assert aborted["status"] == "queued"
    assert answered["status"] == "queued"
    assert approved["status"] == "queued"
    assert retried["status"] == "queued"
    assert calls == [
        (tmp_path, ["task", "Implement KC-432"]),
        (tmp_path, ["abort", "run-432"]),
        (
            tmp_path,
            ["answer", "run-432", "Use the existing allowlist.", "--actor", "dashboard"],
        ),
        (
            tmp_path,
            [
                "approve",
                "--run-id",
                "run-432",
                "--actor",
                "dashboard",
                "--reason",
                "Reviewed locally.",
            ],
        ),
        (tmp_path, ["retry", "run-432"]),
    ]
