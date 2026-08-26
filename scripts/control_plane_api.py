#!/usr/bin/env python3
"""Loopback control-plane API for intake, approvals, recovery, CI, and metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from approval_lifecycle import ApprovalError, approve_run, reject_run, resume_run
from ci_feedback import CIIngestionError, ingest_ci_failure
from event_ingestion import EventError, enqueue_envelope, normalize_event
from operational_metrics import collect_metrics
from task_queue import DEFAULT_DB, TaskQueue


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.observability.dashboard import DASHBOARD_HTML
from ai_harness.task_batch import BatchManifestError, parse_batch_manifest

RUNS_DIR = ROOT / ".agent-runs"
MAX_BODY_BYTES = 1_048_576


class APIError(ValueError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def run_path(runs_dir: Path, run_id: str) -> Path:
    if not run_id or "/" in run_id or ".." in run_id:
        raise APIError(HTTPStatus.BAD_REQUEST, "invalid run id")
    path = (runs_dir / run_id).resolve()
    if path.parent != runs_dir.resolve() or not path.is_dir():
        raise APIError(HTTPStatus.NOT_FOUND, "run not found")
    return path


class ControlPlaneHandler(BaseHTTPRequestHandler):
    queue: TaskQueue
    runs_dir: Path
    auth_token: str
    webhook_secret: str
    default_repository: Path

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: HTTPStatus, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def send_html(self, status: HTTPStatus, value: str) -> None:
        encoded = value.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(encoded)

    def authorize(self) -> None:
        if not self.auth_token:
            return
        if self.headers.get("Authorization", "") != f"Bearer {self.auth_token}":
            raise APIError(HTTPStatus.UNAUTHORIZED, "invalid control-plane token")

    def body(self) -> tuple[bytes, dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "invalid content length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise APIError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body size is invalid")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "request body must be JSON") from exc
        if not isinstance(value, dict):
            raise APIError(HTTPStatus.BAD_REQUEST, "request body must be an object")
        return raw, value

    def agent_command(
        self,
        repository: Path,
        arguments: list[str],
        *,
        timeout: int = 120,
    ) -> dict[str, Any]:
        command = [sys.executable, "-m", "ai_harness", *arguments, "--repo", str(repository), "--json"]
        environment = os.environ.copy()
        environment["AI_HARNESS_HOME"] = str(ROOT)
        try:
            completed = subprocess.run(
                command,
                cwd=repository,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, f"agent command failed: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "agent command failed").strip()[-2000:]
            raise APIError(HTTPStatus.BAD_REQUEST, detail)
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise APIError(HTTPStatus.INTERNAL_SERVER_ERROR, "agent returned an invalid response") from exc
        if not isinstance(value, dict):
            raise APIError(HTTPStatus.INTERNAL_SERVER_ERROR, "agent returned an invalid response")
        return value

    def request_repository(self, payload: dict[str, Any]) -> Path:
        raw_repository = str(payload.get("repository", "")).strip()
        repository = Path(raw_repository or self.default_repository).expanduser().resolve()
        if not repository.is_dir():
            raise APIError(HTTPStatus.BAD_REQUEST, "project folder does not exist")
        return repository

    def submit_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        manifest: Any = payload.get("manifest")
        if manifest is None:
            manifest = {
                "version": payload.get("version", 1),
                "repositories": payload.get("repositories", {}),
                "tasks": payload.get("tasks"),
            }
        try:
            tasks = parse_batch_manifest(manifest, base_dir=self.default_repository)
        except BatchManifestError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        identity = json.dumps(
            [
                {
                    **task,
                    "repository": str(task["repository"]),
                }
                for task in tasks
            ],
            sort_keys=True,
            ensure_ascii=False,
        )
        fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        batch_id = datetime.now(timezone.utc).strftime(
            f"%Y%m%dT%H%M%S.%fZ-batch-{fingerprint}"
        )
        allowed_repositories = sorted({str(task["repository"]) for task in tasks})
        accepted: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for task in tasks:
            arguments = ["task", str(task["goal"]), "--mode", str(task["mode"])]
            if task["task_id"]:
                arguments.extend(["--task-id", str(task["task_id"])])
            if task["parallel"]:
                arguments.append("--worktree")
            if task["priority"]:
                arguments.extend(["--priority", str(task["priority"])])
            if task["max_retries"] != 2:
                arguments.extend(["--max-retries", str(task["max_retries"])])
            if task["max_parallel_tasks"]:
                arguments.extend(
                    ["--max-parallel-tasks", str(task["max_parallel_tasks"])]
                )
            arguments.extend(
                ["--batch-id", batch_id, "--batch-index", str(task["batch_index"])]
            )
            for repository in allowed_repositories:
                arguments.extend(["--allowed-child-repository", repository])
            try:
                accepted.append(self.agent_command(Path(task["repository"]), arguments))
            except APIError as exc:
                errors.append(
                    {
                        "index": task["batch_index"],
                        "repository": str(task["repository"]),
                        "error": str(exc),
                    }
                )
        return {
            "status": "accepted" if not errors else ("partial" if accepted else "error"),
            "batch_id": batch_id,
            "accepted": accepted,
            "errors": errors,
        }

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path in {"/", "/dashboard"}:
                self.send_html(HTTPStatus.OK, DASHBOARD_HTML)
                return
            self.authorize()
            metrics = collect_metrics(runs_dir=self.runs_dir, db_path=self.queue.path)
            if path == "/health":
                self.send_json(HTTPStatus.OK, {"status": "ok", "service": metrics["service"]})
            elif path == "/metrics":
                self.send_json(HTTPStatus.OK, metrics)
            elif path == "/config":
                self.send_json(
                    HTTPStatus.OK,
                    {"default_repository": str(self.default_repository), "refresh_seconds": 5},
                )
            elif path == "/runs":
                self.send_json(HTTPStatus.OK, metrics["runs"])
            elif path == "/workers":
                self.send_json(HTTPStatus.OK, metrics["workers"])
            elif path == "/queue":
                self.send_json(HTTPStatus.OK, metrics["queue"])
            elif path == "/leases":
                self.send_json(HTTPStatus.OK, metrics["leases"])
            elif path == "/budgets":
                self.send_json(HTTPStatus.OK, metrics["budgets"])
            elif path == "/exceptions":
                self.send_json(HTTPStatus.OK, metrics["exceptions"])
            elif path == "/traces":
                self.send_json(HTTPStatus.OK, metrics["tracing"])
            else:
                raise APIError(HTTPStatus.NOT_FOUND, "endpoint not found")
        except APIError as exc:
            self.send_json(exc.status, {"status": "error", "error": str(exc)})

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path != "/webhooks/github/actions":
                self.authorize()
            raw, payload = self.body()
            if path in {"/ui/tasks/batch", "/tasks/batch"}:
                result = self.submit_batch(payload)
                status = HTTPStatus.ACCEPTED if result["accepted"] else HTTPStatus.BAD_REQUEST
                self.send_json(status, result)
                return
            if path == "/ui/tasks":
                repository = self.request_repository(payload)
                goal = str(payload.get("goal", "")).strip()
                if not goal:
                    raise APIError(HTTPStatus.BAD_REQUEST, "describe the task before starting it")
                workspace_mode = (
                    "worktree"
                    if payload.get("parallel") is True
                    else str(payload.get("workspace_mode", payload.get("mode", "new_branch")))
                )
                if workspace_mode not in {"new_branch", "current_branch", "worktree"}:
                    raise APIError(HTTPStatus.BAD_REQUEST, "unknown workspace mode")
                execution_mode = str(payload.get("execution_mode", "auto"))
                if execution_mode not in {"auto", "adaptive", "fast", "full", "goal"}:
                    raise APIError(HTTPStatus.BAD_REQUEST, "unknown execution mode")
                arguments = ["task", goal, "--mode", execution_mode]
                task_id = str(payload.get("task_id", "")).strip()
                if task_id:
                    arguments.extend(["--task-id", task_id])
                if workspace_mode == "current_branch":
                    arguments.append("--current-branch")
                elif workspace_mode == "worktree":
                    arguments.append("--worktree")
                max_parallel = payload.get("max_parallel_tasks", 0)
                if max_parallel:
                    try:
                        selected_limit = int(max_parallel)
                    except (TypeError, ValueError) as exc:
                        raise APIError(
                            HTTPStatus.BAD_REQUEST,
                            "max_parallel_tasks must be an integer",
                        ) from exc
                    if not 1 <= selected_limit <= 32:
                        raise APIError(
                            HTTPStatus.BAD_REQUEST,
                            "max_parallel_tasks must be between 1 and 32",
                        )
                    arguments.extend(["--max-parallel-tasks", str(selected_limit)])
                result = self.agent_command(repository, arguments)
                self.send_json(HTTPStatus.ACCEPTED, result)
                return
            if path in {"/tasks", "/events"}:
                source = str(payload.get("source", "api"))
                repository = Path(str(payload.get("repository", ROOT)))
                event_payload = payload.get("payload", payload)
                if not isinstance(event_payload, dict):
                    raise APIError(HTTPStatus.BAD_REQUEST, "event payload must be an object")
                envelope = normalize_event(
                    source=source,
                    payload=event_payload,
                    repository=repository,
                    project=str(payload.get("project", "agent_workspace")),
                )
                record = enqueue_envelope(self.queue, envelope)
                self.send_json(HTTPStatus.ACCEPTED, {"envelope": envelope, "queue_task": asdict(record)})
                return
            if path == "/webhooks/github/actions":
                feedback, record = ingest_ci_failure(
                    body=raw,
                    signature=self.headers.get("X-Hub-Signature-256", ""),
                    secret=self.webhook_secret,
                    queue=self.queue,
                    runs_dir=self.runs_dir,
                )
                self.send_json(HTTPStatus.ACCEPTED, {"feedback": feedback, "queue_task": asdict(record)})
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["ui", "runs"]:
                run_id, action = parts[2], parts[3]
                repository = self.request_repository(payload)
                if action == "answer":
                    response = str(payload.get("response", "")).strip()
                    if not response:
                        raise APIError(HTTPStatus.BAD_REQUEST, "enter an answer before continuing")
                    arguments = ["answer", run_id, response, "--actor", "dashboard"]
                elif action == "approve":
                    arguments = [
                        "approve",
                        "--run-id",
                        run_id,
                        "--actor",
                        "dashboard",
                        "--reason",
                        str(payload.get("reason", "Approved in the local dashboard.")),
                    ]
                elif action in {"retry", "abort"}:
                    arguments = [action, run_id]
                else:
                    raise APIError(HTTPStatus.NOT_FOUND, "run action not found")
                result = self.agent_command(repository, arguments)
                self.send_json(HTTPStatus.ACCEPTED, result)
                return
            if len(parts) != 3 or parts[0] != "runs":
                raise APIError(HTTPStatus.NOT_FOUND, "endpoint not found")
            run_dir = run_path(self.runs_dir, parts[1])
            action = parts[2]
            actor = str(payload.get("actor", "")).strip()
            reason = str(payload.get("reason", ""))
            scope = payload.get("scope")
            if scope is not None and not isinstance(scope, dict):
                raise APIError(HTTPStatus.BAD_REQUEST, "scope must be an object")
            if action == "approve":
                result = approve_run(run_dir, actor=actor, scope=scope, reason=reason)
                self.send_json(HTTPStatus.OK, result)
            elif action == "reject":
                result = reject_run(run_dir, actor=actor, reason=reason)
                self.send_json(HTTPStatus.OK, result)
            elif action == "resume":
                transition, record = resume_run(run_dir, queue=self.queue)
                self.send_json(HTTPStatus.ACCEPTED, {**transition, "queue_task": asdict(record)})
            else:
                raise APIError(HTTPStatus.NOT_FOUND, "run action not found")
        except APIError as exc:
            self.send_json(exc.status, {"status": "error", "error": str(exc)})
        except (ApprovalError, CIIngestionError, EventError, OSError, ValueError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": str(exc)})


def handler_factory(
    *,
    queue: TaskQueue,
    runs_dir: Path,
    auth_token: str,
    webhook_secret: str,
    default_repository: Path = ROOT,
) -> type[ControlPlaneHandler]:
    class ConfiguredHandler(ControlPlaneHandler):
        pass

    ConfiguredHandler.queue = queue
    ConfiguredHandler.runs_dir = runs_dir
    ConfiguredHandler.auth_token = auth_token
    ConfiguredHandler.webhook_secret = webhook_secret
    ConfiguredHandler.default_repository = default_repository.resolve()
    return ConfiguredHandler


def serve_control_plane(
    *,
    host: str,
    port: int,
    db_path: Path,
    runs_dir: Path,
    auth_token: str,
    webhook_secret: str = "",
    default_repository: Path = ROOT,
    on_ready: Callable[[int], None] | None = None,
) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("control-plane API may only bind to loopback")
    server = ThreadingHTTPServer(
        (host, port),
        handler_factory(
            queue=TaskQueue(db_path),
            runs_dir=runs_dir.resolve(),
            auth_token=auth_token,
            webhook_secret=webhook_secret,
            default_repository=default_repository,
        ),
    )
    try:
        if on_ready is not None:
            on_ready(server.server_port)
        server.serve_forever()
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    auth_token = os.environ.get("AGENT_CONTROL_PLANE_TOKEN", "")
    try:
        serve_control_plane(
            host=args.host,
            port=args.port,
            db_path=args.db,
            runs_dir=args.runs_dir,
            auth_token=auth_token,
            webhook_secret=os.environ.get("AGENT_GITHUB_WEBHOOK_SECRET", ""),
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
