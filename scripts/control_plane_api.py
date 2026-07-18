#!/usr/bin/env python3
"""Loopback control-plane API for intake, approvals, recovery, CI, and metrics."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from approval_lifecycle import ApprovalError, approve_run, reject_run, resume_run
from ci_feedback import CIIngestionError, ingest_ci_failure
from event_ingestion import EventError, enqueue_envelope, normalize_event
from operational_metrics import collect_metrics
from task_queue import DEFAULT_DB, TaskQueue


ROOT = Path(__file__).resolve().parents[1]
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

    def do_GET(self) -> None:
        try:
            self.authorize()
            metrics = collect_metrics(runs_dir=self.runs_dir, db_path=self.queue.path)
            path = urlparse(self.path).path
            if path == "/health":
                self.send_json(HTTPStatus.OK, {"status": "ok", "service": metrics["service"]})
            elif path == "/metrics":
                self.send_json(HTTPStatus.OK, metrics)
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
    *, queue: TaskQueue, runs_dir: Path, auth_token: str, webhook_secret: str
) -> type[ControlPlaneHandler]:
    class ConfiguredHandler(ControlPlaneHandler):
        pass

    ConfiguredHandler.queue = queue
    ConfiguredHandler.runs_dir = runs_dir
    ConfiguredHandler.auth_token = auth_token
    ConfiguredHandler.webhook_secret = webhook_secret
    return ConfiguredHandler


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
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("control-plane API may only bind to loopback")
    server = ThreadingHTTPServer(
        (args.host, args.port),
        handler_factory(
            queue=TaskQueue(args.db),
            runs_dir=args.runs_dir.resolve(),
            auth_token=auth_token,
            webhook_secret=os.environ.get("AGENT_GITHUB_WEBHOOK_SECRET", ""),
        ),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
