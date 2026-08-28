from __future__ import annotations

import json
import hashlib
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


def test_metrics_expose_bounded_structured_question(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run = runs / "question-run"
    write_json(
        run / "workflow.json",
        {
            "run_id": "question-run",
            "task_id": "question-task",
            "repository": str(tmp_path),
            "execution_status": "awaiting_approval",
            "attention": {
                "required": True,
                "summary": "Choose an environment.",
                "details": ["Select one option."],
                "action": "answer",
                "question": {
                    "id": "environment",
                    "options": [
                        {
                            "label": "Local",
                            "description": "Use local services.",
                            "value": "local",
                            "recommended": True,
                            "requires_input": True,
                        },
                        {
                            "label": "Staging",
                            "description": "Use shared services.",
                            "value": "staging",
                            "recommended": False,
                            "requires_input": False,
                        },
                    ],
                    "allow_custom": True,
                },
            },
        },
    )

    metrics = collect_metrics(runs_dir=runs, db_path=tmp_path / "queue.db")

    attention = metrics["runs"]["items"][0]["attention"]
    assert attention["action"] == "answer"
    assert attention["question"]["id"] == "environment"
    assert attention["question"]["options"][0]["recommended"] is True
    assert attention["question"]["options"][0]["requires_input"] is True
    assert attention["question"]["options"][1]["requires_input"] is False


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


def test_metrics_project_execution_plan_and_efficiency_for_run(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    run = runs / "adaptive-run"
    write_json(
        run / "workflow.json",
        {
            "run_id": "adaptive-run",
            "task_id": "fix-login",
            "project": "agent_workspace",
            "effective_mode": "adaptive",
            "execution_status": "completed",
            "elapsed_seconds": 258,
            "roles": [
                {
                    "role": "implementation-agent",
                    "llm_invoked": True,
                    "execution_profile": {"execution_profile": "balanced", "model": "gpt-balanced"},
                    "result": {"status": "completed"},
                },
                {
                    "role": "quality-runner",
                    "llm_invoked": False,
                    "result": {"status": "completed"},
                },
            ],
        },
    )
    write_json(
        run / "execution-plan.json",
        {
            "workflow_version": 1,
            "analysis": {"task_class": "bugfix", "scope": "small", "risk": "low"},
            "nodes": [
                {
                    "id": "implementation-agent",
                    "role": "implementation-agent",
                    "execution_kind": "llm_role",
                    "mandatory": True,
                    "model_profile": "balanced",
                    "deterministic_checks": [],
                    "reason": "code change",
                },
                {
                    "id": "quality-runner",
                    "role": "quality-runner",
                    "execution_kind": "harness_stage",
                    "mandatory": True,
                    "model_profile": "",
                    "deterministic_checks": ["lint", "tests"],
                    "reason": "deterministic gate",
                },
            ],
            "skipped_roles": ["planner", "architecture-consistency-agent"],
            "model_profiles": {"implementation-agent": "balanced"},
            "reasoning": {"planner": "small task"},
        },
    )
    write_json(
        run / "metrics.json",
        {
            "model_calls_per_task": 1,
            "input_tokens_per_task": 66_031,
            "uncached_input_tokens_per_task": 24_821,
            "output_tokens_per_task": 3_200,
            "context_cache_hit_rate": 0.62,
            "roles_executed_per_task": 2,
            "roles_skipped_per_task": 2,
            "model_escalations_per_task": 0,
            "repair_attempts_per_task": 1,
            "time_to_success": 258,
            "roles": [{"role": "implementation-agent", "model": "gpt-balanced"}],
        },
    )

    metrics = collect_metrics(runs_dir=runs, db_path=tmp_path / "queue.db")

    detail = metrics["runs"]["items"][0]["adaptive"]
    assert detail["mode"] == "adaptive"
    assert detail["task_class"] == "bugfix"
    assert detail["efficiency"]["cached_input_tokens"] == 41_210
    assert detail["efficiency"]["repair_loops"] == 1
    nodes = {item["role"]: item for item in detail["execution_plan"]}
    assert nodes["quality-runner"]["deterministic"] is True
    assert nodes["implementation-agent"]["model_profile"] == "balanced"
    assert nodes["planner"]["state"] == "skipped"


def test_metrics_read_only_fingerprint_verified_adaptive_acceptance(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    report_path = runs / "eval-run" / "adaptive-ab-report.json"
    report = {
        "schema_version": 1,
        "kind": "adaptive_ab_acceptance",
        "status": "pass",
        "adaptive_default_allowed": True,
        "evidence_kind": "paired_authoritative_runs",
        "paired_tasks": 50,
        "acceptance_summary": [
            {"key": "model_calls", "label": "Model calls", "value": -47, "unit": "percent", "status": "pass"}
        ],
        "comparison": [
            {
                "key": "model_calls_per_task",
                "label": "Model Calls / Task",
                "full": 8.4,
                "adaptive": 4.6,
                "delta": -45.2,
                "format": "number",
                "delta_format": "percent_change",
            }
        ],
        "breakdowns": {"scope": [{"value": "small", "paired_tasks": 10, "comparison": []}]},
        "pairs": [{"case_id": "bug-01", "full": {"scope": "small"}, "adaptive": {"scope": "small"}}],
        "blockers": [],
    }
    write_json(report_path, report)
    fingerprint = "sha256:" + hashlib.sha256(report_path.read_bytes()).hexdigest()
    decision_path = tmp_path / "adaptive_execution_acceptance.json"
    write_json(
        decision_path,
        {
            "schema_version": 1,
            "status": "pass",
            "adaptive_default_allowed": True,
            "dataset_cases": 50,
            "evidence_kind": "paired_authoritative_runs",
            "report_path": str(report_path),
            "report_fingerprint": fingerprint,
            "blockers": [],
        },
    )

    accepted = collect_metrics(
        runs_dir=runs,
        db_path=tmp_path / "queue.db",
        adaptive_acceptance_path=decision_path,
    )["adaptive"]
    assert accepted["display_status"] == "PASS"
    assert accepted["overall"] == "READY FOR DEFAULT"
    assert accepted["comparison"][0]["delta"] == -45.2
    assert accepted["pairs"][0]["case_id"] == "bug-01"

    report_path.write_text(report_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    stale = collect_metrics(
        runs_dir=runs,
        db_path=tmp_path / "queue.db",
        adaptive_acceptance_path=decision_path,
    )["adaptive"]
    assert stale["display_status"] == "NOT ENOUGH DATA"
    assert stale["adaptive_default_allowed"] is False
    assert stale["comparison"] == []

    write_json(
        decision_path,
        {
            "schema_version": 1,
            "status": "fail",
            "adaptive_default_allowed": False,
            "dataset_cases": 50,
            "report_path": "",
            "blockers": ["median_duration_reduced_25_percent"],
        },
    )
    rejected = collect_metrics(
        runs_dir=runs,
        db_path=tmp_path / "queue.db",
        adaptive_acceptance_path=decision_path,
    )["adaptive"]
    assert rejected["display_status"] == "FAIL"
    assert rejected["overall"] == "NOT READY"

    write_json(
        decision_path,
        {
            "schema_version": 1,
            "status": "not_evaluated",
            "adaptive_default_allowed": False,
            "dataset_cases": "invalid",
            "report_path": "",
            "blockers": [],
        },
    )
    malformed = collect_metrics(
        runs_dir=runs,
        db_path=tmp_path / "queue.db",
        adaptive_acceptance_path=decision_path,
    )["adaptive"]
    assert malformed["display_status"] == "NOT ENOUGH DATA"
    assert malformed["dataset_cases"] == 0


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
        assert 'id="executionMode"' in dashboard
        assert '<option value="fast">' in dashboard
        assert '<option value="full">' in dashboard
        assert '<option value="goal">' in dashboard
        assert "Долгая цель — до 4 часов" in dashboard
        assert 'id="workspaceMode"' in dashboard
        assert 'id="workspaceModeNote"' in dashboard
        assert 'id="parallelTask"' in dashboard
        assert '<option value="adaptive">' in dashboard
        assert 'id="batchRows"' in dashboard
        assert 'id="addBatchTask"' in dashboard
        assert 'id="batchConcurrency"' in dashboard
        assert "batch-repository" in dashboard
        assert "batch-goal" in dashboard
        assert "batch-parallel" in dashboard
        assert 'id="batchManifest"' in dashboard
        assert 'id="fillBatchExample"' in dashboard
        assert 'id="clearBatch"' in dashboard
        assert "Запустить несколько задач" in dashboard
        assert "Расширенный режим: импорт YAML" in dashboard
        assert "разбираться в Git не нужно" in dashboard
        assert "max_parallel_tasks" in dashboard
        assert "Queued" in dashboard
        assert "PR ready" in dashboard
        assert 'id="repositoryFilter"' in dashboard
        assert 'id="branchFilter"' in dashboard
        assert 'id="workerFilter"' in dashboard
        assert 'id="clearFilters"' in dashboard
        assert 'id="clearHistory"' in dashboard
        assert 'id="restoreHistory"' in dashboard
        assert "historyCanHide(task){return ['completed','cancelled'].includes(task.status)}" in dashboard
        assert "Активные задачи, ошибки, журналы и файлы сохранятся" in dashboard
        assert "localStorage.setItem(historyStorageKey" in dashboard
        assert "Быстрый старт" in dashboard
        assert "Другой ответ" in dashboard
        assert "question.options" in dashboard
        assert "item.requires_input" in dashboard
        assert "Добавьте данные для выбранного варианта" in dashboard
        assert "choice: ${select.value}" in dashboard
        assert "answers:{}" in dashboard
        assert "contains(document.activeElement)" in dashboard
        assert 'id="adaptivePanel"' in dashboard
        assert "Adaptive Acceptance" in dashboard
        assert 'id="adaptiveComparison"' in dashboard
        assert 'id="adaptiveTaskClass"' in dashboard
        assert 'id="adaptiveScope"' in dashboard
        assert 'id="adaptiveRisk"' in dashboard
        assert 'id="adaptiveRepository"' in dashboard
        assert 'id="adaptiveModel"' in dashboard
        assert 'id="adaptiveRole"' in dashboard
        assert 'id="adaptiveOutcome"' in dashboard
        assert 'id="adaptiveMode"' in dashboard
        assert "Execution plan & efficiency" in dashboard
        assert "model_calls_reduced_40_percent" not in dashboard
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
        adaptive = api_request(f"{base}/adaptive", access_key)
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
    assert adaptive["display_status"] in {"PASS", "FAIL", "NOT ENOUGH DATA"}
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
            body={
                "repository": str(tmp_path),
                "goal": "Implement KC-432",
                "workspace_mode": "worktree",
                "execution_mode": "fast",
            },
        )
        goal_launched = api_request(
            f"{base}/ui/tasks",
            token,
            method="POST",
            body={
                "repository": str(tmp_path),
                "goal": "Execute checkpointed objective",
                "workspace_mode": "worktree",
                "execution_mode": "goal",
            },
        )
        legacy_launched = api_request(
            f"{base}/ui/tasks",
            token,
            method="POST",
            body={
                "repository": str(tmp_path),
                "goal": "Continue legacy dashboard task",
                "mode": "current_branch",
            },
        )
        try:
            api_request(
                f"{base}/ui/tasks",
                token,
                method="POST",
                body={
                    "repository": str(tmp_path),
                    "goal": "Invalid dashboard mode",
                    "execution_mode": "turbo",
                },
            )
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("dashboard must reject an unknown execution mode")
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
    assert goal_launched["task_id"] == "kc-432"
    assert legacy_launched["task_id"] == "kc-432"
    assert aborted["status"] == "queued"
    assert answered["status"] == "queued"
    assert approved["status"] == "queued"
    assert retried["status"] == "queued"
    assert calls == [
        (tmp_path, ["task", "Implement KC-432", "--mode", "fast", "--worktree"]),
        (
            tmp_path,
            ["task", "Execute checkpointed objective", "--mode", "goal", "--worktree"],
        ),
        (
            tmp_path,
            ["task", "Continue legacy dashboard task", "--mode", "auto", "--current-branch"],
        ),
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


def test_dashboard_batch_maps_parallel_to_worktree_and_repository_limit(
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
        return {"status": "queued", "task_id": arguments[1], "timeout": timeout}

    monkeypatch.setattr(ControlPlaneHandler, "agent_command", command)
    queue = TaskQueue(tmp_path / "queue.db")
    token = "dashboard-batch-token"
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
        result = api_request(
            f"{base}/ui/tasks/batch",
            token,
            method="POST",
            body={
                "version": 1,
                "repositories": {
                    "project": {"path": str(tmp_path), "max_parallel_tasks": 2},
                },
                "tasks": [
                    {"repo": "project", "goal": "First task"},
                    {"repo": "project", "goal": "Parallel task", "parallel": True},
                ],
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result["status"] == "accepted"
    assert len(result["accepted"]) == 2
    assert "--worktree" not in calls[0][1]
    assert "--worktree" in calls[1][1]
    assert calls[0][1][calls[0][1].index("--max-parallel-tasks") + 1] == "2"
    assert "--batch-id" in calls[0][1]
