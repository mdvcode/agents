#!/usr/bin/env python3
"""Loopback control-plane API for intake, approvals, recovery, CI, and metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

from approval_lifecycle import ApprovalError, approve_run, reject_run, resume_run
from ci_feedback import CIIngestionError, ingest_ci_failure
from event_ingestion import EventError, enqueue_envelope, normalize_event
from operational_metrics import collect_metrics
from task_queue import DEFAULT_DB, TaskQueue


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.observability import dashboard as dashboard_module
from ai_harness.attachments import (
    MAX_RUNTIME_IMAGE_REFERENCES,
    AttachmentError,
    AttachmentLimits,
    AttachmentQuotaError,
    AttachmentStore,
    IncomingAttachment,
)
from ai_harness.attachments.runtime import (
    DEFAULT_MAX_TEXT_BYTES_PER_REFERENCE,
    DEFAULT_MAX_TOTAL_TEXT_BYTES,
)
from ai_harness.project import (
    CONFIG_RELATIVE_PATH,
    ProjectConfigError,
    load_project_config,
    project_attachment_limits,
    project_is_trusted,
)
from ai_harness.task_batch import BatchManifestError, parse_batch_manifest

RUNS_DIR = ROOT / ".agent-runs"
ATTACHMENT_STORE_ROOT = ROOT / ".agent-uploads"
MAX_BODY_BYTES = 1_048_576
# Binding may revalidate five PDFs for up to 30 seconds each and restage a
# multi-gigabyte configured task. Keep that transaction bounded without using
# the ordinary 120-second command timeout that can interrupt its rollback path.
ATTACHMENT_TASK_TIMEOUT_SECONDS = 10 * 60
ATTACHMENT_CLEANUP_INTERVAL_SECONDS = 15 * 60
DASHBOARD_HTML = dashboard_module.DASHBOARD_HTML
ASSET_DIR = Path(dashboard_module.__file__).resolve().parent / "assets"
PUBLIC_ASSETS = {
    "tweebit-icon-16.png": "image/png",
    "tweebit-icon-24.png": "image/png",
    "tweebit-icon-32.png": "image/png",
    "tweebit-icon-48.png": "image/png",
    "tweebit-icon-128.png": "image/png",
    "tweebit-wordmark.svg": "image/svg+xml",
}


class BoundedRequestStream:
    """Expose exactly one declared HTTP body without waiting for socket EOF."""

    def __init__(self, source: Any, length: int) -> None:
        self.source = source
        self.remaining = length

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        selected = self.remaining if size is None or size < 0 else min(size, self.remaining)
        chunk = self.source.read(selected)
        if not chunk:
            raise OSError("attachment upload ended before Content-Length bytes were received")
        self.remaining -= len(chunk)
        return chunk


class APIError(ValueError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def cleanup_attachment_uploads(
    attachment_store_root: Path, *, now: float | None = None
) -> tuple[str, ...]:
    """Remove expired, unbound uploads independently of future upload traffic."""

    try:
        return AttachmentStore(attachment_store_root).cleanup_expired(now=now)
    except AttachmentError:
        # A malformed entry must not stop the loopback control plane. Individual
        # intake paths still surface validation failures to the authenticated user.
        return ()


def attachment_cleanup_loop(
    attachment_store_root: Path,
    stop: threading.Event,
    *,
    interval_seconds: float = ATTACHMENT_CLEANUP_INTERVAL_SECONDS,
) -> None:
    cleanup_attachment_uploads(attachment_store_root)
    while not stop.wait(interval_seconds):
        cleanup_attachment_uploads(attachment_store_root)


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
    attachment_store_root: Path
    cli_mutations_enabled: bool
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
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(encoded)

    def send_asset(self, name: str) -> None:
        content_type = PUBLIC_ASSETS.get(name)
        if content_type is None:
            raise APIError(HTTPStatus.NOT_FOUND, "asset not found")
        path = ASSET_DIR / name
        try:
            encoded = path.read_bytes()
        except OSError as exc:
            raise APIError(HTTPStatus.NOT_FOUND, "asset not found") from exc
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def authorize(self) -> None:
        if not self.auth_token:
            if self.command not in {"GET", "HEAD"}:
                raise APIError(
                    HTTPStatus.FORBIDDEN,
                    "a control-plane bearer token is required for mutations",
                )
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

    def attachment_store(
        self, limits: AttachmentLimits | None = None
    ) -> AttachmentStore:
        store = AttachmentStore(self.attachment_store_root, limits=limits)
        store.cleanup_expired()
        return store

    def require_cli_mutations_enabled(self) -> None:
        if not self.cli_mutations_enabled:
            raise APIError(
                HTTPStatus.CONFLICT,
                "dashboard CLI mutations are disabled because the configured queue or "
                "runs directory does not match this Harness installation",
            )

    def repository_settings(
        self, repository: Path
    ) -> tuple[AttachmentLimits, str, tuple[str, ...], bool]:
        """Return only locally trusted project overrides, with safe defaults."""

        config_path = repository.resolve() / CONFIG_RELATIVE_PATH
        if not config_path.is_file():
            return AttachmentLimits(), "codex-sdk", ("text", "local_image"), False
        try:
            config = load_project_config(repository)
            trusted = project_is_trusted(config)
        except ProjectConfigError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        if not trusted:
            return AttachmentLimits(), "codex-sdk", ("text", "local_image"), False
        capabilities = (
            ("text", "local_image")
            if config.runtime_provider == "codex-sdk"
            else ("text",)
        )
        return (
            project_attachment_limits(config),
            config.runtime_provider,
            capabilities,
            True,
        )

    def query_repository(self, values: dict[str, list[str]]) -> Path:
        raw_repository = str(values.get("repository", [""])[0]).strip()
        repository = Path(raw_repository or self.default_repository).expanduser().resolve()
        if not repository.is_dir():
            raise APIError(HTTPStatus.BAD_REQUEST, "project folder does not exist")
        return repository

    def stage_attachment(self) -> dict[str, Any]:
        parsed = urlparse(self.path)
        values = parse_qs(parsed.query, keep_blank_values=True)
        repository = self.query_repository(values)
        filename = str(values.get("name", [""])[0]).strip()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "invalid attachment content length") from exc
        limits, runtime_provider, _capabilities, _trusted = self.repository_settings(
            repository
        )
        if length <= 0:
            raise APIError(HTTPStatus.BAD_REQUEST, "attachment body is empty")
        if length > limits.max_file_bytes:
            raise APIError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"attachment exceeds the {limits.max_file_bytes // (1024 * 1024)} MiB file limit",
            )
        if runtime_provider == "codex-cli" and Path(filename).suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
        }:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "the codex-cli runtime accepts text files and PDFs, not direct images",
            )
        store = self.attachment_store(limits)
        try:
            staged = store.stage(
                [
                    IncomingAttachment(
                        filename=filename,
                        stream=BoundedRequestStream(self.rfile, length),
                        declared_mime=self.headers.get("Content-Type", ""),
                    )
                ]
            )
        except AttachmentQuotaError as exc:
            raise APIError(
                HTTPStatus.INSUFFICIENT_STORAGE,
                "the private pending-attachment pool is full; start or remove an existing task and retry",
            ) from exc
        attachment = staged.manifest["attachments"][0]
        pdf = attachment.get("pdf", {})
        issues = pdf.get("issues", []) if isinstance(pdf, dict) else []
        if staged.status != "complete" or issues:
            try:
                store.discard_staged([staged.set_id])
            except AttachmentError:
                pass
            codes = [
                str(issue.get("code", "pdf_processing_incomplete"))
                for issue in issues
                if isinstance(issue, dict)
            ]
            detail = ", ".join(codes[:5]) or "pdf_processing_incomplete"
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "PDF context must be processed completely before the task can start "
                f"({detail}); reduce or repair the PDF and upload it again",
            )
        runtime_image_references = sum(
            1
            for item in attachment.get("content", [])
            if isinstance(item, dict) and item.get("kind") == "local_image"
        )
        if runtime_provider == "codex-cli" and runtime_image_references:
            try:
                store.discard_staged([staged.set_id])
            except AttachmentError:
                pass
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "this PDF contains scanned/image pages; use a PDF with a text layer or configure codex-sdk",
            )
        return {
            "status": staged.status,
            "set_id": staged.set_id,
            "attachment": {
                key: attachment[key]
                for key in ("id", "safe_name", "kind", "media_type", "size")
                if key in attachment
            }
            | {"runtime_image_references": runtime_image_references},
            "issues": issues,
            "limits": {
                "max_files": limits.max_files,
                "max_file_bytes": limits.max_file_bytes,
                "max_task_bytes": limits.max_task_bytes,
            },
        }

    def validate_attachment_sets(
        self, repository: Path, set_ids: list[str]
    ) -> AttachmentLimits:
        limits, runtime_provider, capabilities, _trusted = self.repository_settings(
            repository
        )
        if len(set_ids) > limits.max_files:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                f"no more than {limits.max_files} attachments are allowed",
            )
        if len(set(set_ids)) != len(set_ids):
            raise APIError(
                HTTPStatus.BAD_REQUEST, "duplicate attachment set ids are not allowed"
            )
        total_files = 0
        total_bytes = 0
        has_local_images = False
        local_image_references = 0
        store = self.attachment_store(limits)
        for set_id in set_ids:
            attachment_set = store.load(set_id)
            local_image_references += store.validate_runtime_ready(attachment_set)
            attachments = attachment_set.manifest.get("attachments", [])
            if not isinstance(attachments, list):
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid attachment manifest")
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    raise APIError(HTTPStatus.BAD_REQUEST, "invalid attachment manifest")
                size = attachment.get("size")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    raise APIError(HTTPStatus.BAD_REQUEST, "invalid attachment size")
                if size > limits.max_file_bytes:
                    raise APIError(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "an attachment exceeds this project's per-file limit",
                    )
                total_files += 1
                total_bytes += size
                content = attachment.get("content", [])
                if isinstance(content, list) and any(
                    isinstance(item, dict) and item.get("kind") == "local_image"
                    for item in content
                ):
                    has_local_images = True
        if total_files > limits.max_files:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                f"no more than {limits.max_files} attachments are allowed",
            )
        if total_bytes > limits.max_task_bytes:
            raise APIError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "attachments exceed this project's total task limit",
            )
        if local_image_references > MAX_RUNTIME_IMAGE_REFERENCES:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "attachments contain more than "
                f"{MAX_RUNTIME_IMAGE_REFERENCES} runtime image references",
            )
        if has_local_images and "local_image" not in capabilities:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                f"the {runtime_provider} runtime cannot receive image attachment context",
            )
        return limits

    def agent_command(
        self,
        repository: Path,
        arguments: list[str],
        *,
        timeout: int = 120,
    ) -> dict[str, Any]:
        command = [sys.executable, "-m", "ai_harness", *arguments, "--repo", str(repository), "--json"]
        environment = os.environ.copy()
        # The CLI resolves staged attachment set ids below AI_HARNESS_HOME. Keep
        # the subprocess home and the HTTP upload store on the same explicit
        # root even when runs are written to a custom --runs-dir.
        environment["AI_HARNESS_HOME"] = str(self.attachment_store_root.parent)
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
        self.require_cli_mutations_enabled()
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
            if path.startswith("/assets/"):
                self.send_asset(path.removeprefix("/assets/"))
                return
            self.authorize()
            metrics = collect_metrics(runs_dir=self.runs_dir, db_path=self.queue.path)
            if path == "/health":
                self.send_json(HTTPStatus.OK, {"status": "ok", "service": metrics["service"]})
            elif path == "/metrics":
                self.send_json(HTTPStatus.OK, metrics)
            elif path == "/adaptive":
                self.send_json(HTTPStatus.OK, metrics["adaptive"])
            elif path == "/config":
                parsed = urlparse(self.path)
                repository = self.query_repository(
                    parse_qs(parsed.query, keep_blank_values=True)
                )
                limits, runtime_provider, capabilities, trusted = (
                    self.repository_settings(repository)
                )
                accepted = (
                    ["text", "pdf", "png", "jpeg", "gif"]
                    if "local_image" in capabilities
                    else ["text", "pdf"]
                )
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "default_repository": str(self.default_repository),
                        "repository": str(repository),
                        "refresh_seconds": 5,
                        "product_name": "Tweebit AI Harness by Daryna",
                        "runtime_provider": runtime_provider,
                        "capabilities": list(capabilities),
                        "project_config_trusted": trusted,
                        "attachments": {
                            "enabled": True,
                            "runtime_context_enabled": True,
                            "runtime_consent_required": True,
                            "scanned_pdf_pages_supported": "local_image" in capabilities,
                            "endpoint": "/ui/attachments",
                            "max_files": limits.max_files,
                            "max_file_bytes": limits.max_file_bytes,
                            "max_task_bytes": limits.max_task_bytes,
                            "max_runtime_image_bytes": limits.max_runtime_image_bytes,
                            "max_runtime_image_references": MAX_RUNTIME_IMAGE_REFERENCES,
                            "max_initial_text_bytes": DEFAULT_MAX_TOTAL_TEXT_BYTES,
                            "max_initial_text_bytes_per_reference": DEFAULT_MAX_TEXT_BYTES_PER_REFERENCE,
                            "ttl_hours": limits.ttl_seconds // 3600,
                            "accepted": accepted,
                        },
                    },
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
            if path == "/ui/attachments":
                self.require_cli_mutations_enabled()
                self.send_json(HTTPStatus.CREATED, self.stage_attachment())
                return
            raw, payload = self.body()
            if path in {"/ui/tasks/batch", "/tasks/batch"}:
                result = self.submit_batch(payload)
                status = HTTPStatus.ACCEPTED if result["accepted"] else HTTPStatus.BAD_REQUEST
                self.send_json(status, result)
                return
            if path == "/ui/tasks":
                self.require_cli_mutations_enabled()
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
                attachment_set_ids = payload.get("attachment_set_ids", [])
                if not isinstance(attachment_set_ids, list) or not all(
                    isinstance(value, str) and value for value in attachment_set_ids
                ):
                    raise APIError(
                        HTTPStatus.BAD_REQUEST,
                        "attachment_set_ids must be a list of ids",
                    )
                if attachment_set_ids:
                    self.validate_attachment_sets(repository, attachment_set_ids)
                    if payload.get("attachment_runtime_consent") is not True:
                        raise APIError(
                            HTTPStatus.BAD_REQUEST,
                            "confirm that selected attachments may be sent to the configured AI runtime",
                        )
                    for set_id in attachment_set_ids:
                        arguments.extend(["--attachment-set", set_id])
                    arguments.append("--attachment-runtime-consent")
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
                result = self.agent_command(
                    repository,
                    arguments,
                    timeout=(
                        ATTACHMENT_TASK_TIMEOUT_SECONDS
                        if attachment_set_ids
                        else 120
                    ),
                )
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
                if action not in {"answer", "approve", "retry", "abort"}:
                    raise APIError(HTTPStatus.NOT_FOUND, "run action not found")
                self.require_cli_mutations_enabled()
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
        except (ApprovalError, AttachmentError, CIIngestionError, EventError, OSError, ValueError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": str(exc)})


def handler_factory(
    *,
    queue: TaskQueue,
    runs_dir: Path,
    auth_token: str,
    webhook_secret: str,
    default_repository: Path = ROOT,
    attachment_store_root: Path = ATTACHMENT_STORE_ROOT,
    cli_mutations_enabled: bool = True,
) -> type[ControlPlaneHandler]:
    class ConfiguredHandler(ControlPlaneHandler):
        pass

    ConfiguredHandler.queue = queue
    ConfiguredHandler.runs_dir = runs_dir
    ConfiguredHandler.attachment_store_root = attachment_store_root.resolve()
    ConfiguredHandler.cli_mutations_enabled = cli_mutations_enabled is True
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
    attachment_store_root: Path = ATTACHMENT_STORE_ROOT,
    on_ready: Callable[[int], None] | None = None,
) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("control-plane API may only bind to loopback")
    resolved_store_root = attachment_store_root.resolve()
    harness_home = resolved_store_root.parent
    cli_mutations_enabled = (
        db_path.resolve() == (harness_home / ".agent-queue" / "tasks.db").resolve()
        and runs_dir.resolve() == (harness_home / ".agent-runs").resolve()
    )
    server = ThreadingHTTPServer(
        (host, port),
        handler_factory(
            queue=TaskQueue(db_path),
            runs_dir=runs_dir.resolve(),
            auth_token=auth_token,
            webhook_secret=webhook_secret,
            default_repository=default_repository,
            attachment_store_root=resolved_store_root,
            cli_mutations_enabled=cli_mutations_enabled,
        ),
    )
    cleanup_stop = threading.Event()
    cleanup_thread = threading.Thread(
        target=attachment_cleanup_loop,
        args=(resolved_store_root, cleanup_stop),
        name="tweebit-attachment-cleanup",
        daemon=True,
    )
    cleanup_thread.start()
    try:
        if on_ready is not None:
            on_ready(server.server_port)
        server.serve_forever()
    finally:
        cleanup_stop.set()
        cleanup_thread.join(timeout=2)
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
