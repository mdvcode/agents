#!/usr/bin/env python3
"""Verify and ingest GitHub Actions failures into an existing run and PR repair flow."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from runtime_contracts import load_json as load_schema, validate_contract
from run_state import continuation_attachment_payload
from task_queue import DEFAULT_DB, TaskQueue, TaskRecord
from tool_governance import audit_tool_call, authorize_tool_call


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".agent-runs"
CI_FEEDBACK_SCHEMA = ROOT / "schemas" / "ci_feedback.schema.json"
CommandRunner = Callable[[Sequence[str]], tuple[int, str, str]]
SECRET_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{12,}"),
]


class CIIngestionError(ValueError):
    """Raised when CI feedback is invalid, unauthenticated, or cannot be routed."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CIIngestionError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CIIngestionError(f"{path.name} must contain an object")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_feedback(run_dir: Path, feedback: dict[str, Any]) -> None:
    errors = validate_contract(feedback, load_schema(CI_FEEDBACK_SCHEMA), "ci_feedback")
    if errors:
        raise CIIngestionError("invalid CI feedback artifact: " + "; ".join(errors))
    write_json_atomic(run_dir / "artifacts" / "ci_feedback.json", feedback)


def verify_github_signature(body: bytes, signature: str, secret: str) -> None:
    if not secret:
        raise CIIngestionError("GitHub webhook secret is not configured")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise CIIngestionError("GitHub webhook signature is invalid")


def sanitize_logs(value: str) -> str:
    sanitized = value
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized[-200_000:]


def default_command_runner(args: Sequence[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            list(args),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError) as exc:
        return 127, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def find_run(payload: dict[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    explicit = payload.get("agent_run_id")
    if isinstance(explicit, str) and explicit:
        candidate = (runs_dir / explicit).resolve()
        if candidate.parent == runs_dir.resolve() and candidate.is_dir():
            return candidate
    workflow_run = payload.get("workflow_run", {})
    branch = str(workflow_run.get("head_branch", "")) if isinstance(workflow_run, dict) else ""
    if not branch:
        raise CIIngestionError("CI event has no agent_run_id or head branch")
    matches: list[Path] = []
    for publication_path in runs_dir.glob("*/artifacts/publication.json"):
        publication = read_json(publication_path)
        if publication.get("branch") == branch and publication.get("pr_created_or_updated") is True:
            matches.append(publication_path.parents[1])
    if len(matches) != 1:
        raise CIIngestionError(f"CI branch maps to {len(matches)} workflow runs; expected exactly one")
    return matches[0]


def fetch_failed_logs(
    run_dir: Path,
    external_run_id: str,
    command_runner: CommandRunner = default_command_runner,
) -> str:
    decision = authorize_tool_call(
        role="ci-ingestion",
        tool="github",
        action="read_ci_logs",
        domain="github.com",
        credential_type="gh_auth",
        timeout_seconds=120,
    )
    audit_tool_call(run_dir, decision, phase="ci-ingestion")
    if not decision.allowed:
        raise CIIngestionError(f"tool governance denied CI log read: {decision.reason}")
    returncode, stdout, stderr = command_runner(["gh", "run", "view", external_run_id, "--log-failed"])
    if returncode != 0:
        raise CIIngestionError(f"cannot fetch failed GitHub Actions logs: {stderr or stdout}")
    return sanitize_logs(stdout)


def ingest_ci_failure(
    *,
    body: bytes,
    signature: str,
    secret: str,
    queue: TaskQueue,
    runs_dir: Path = RUNS_DIR,
    command_runner: CommandRunner = default_command_runner,
) -> tuple[dict[str, Any], TaskRecord]:
    verify_github_signature(body, signature, secret)
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise CIIngestionError("GitHub webhook payload must be an object")
    workflow_run = payload.get("workflow_run", {})
    if not isinstance(workflow_run, dict):
        raise CIIngestionError("workflow_run payload is missing")
    if workflow_run.get("status") != "completed" or workflow_run.get("conclusion") != "failure":
        raise CIIngestionError("only completed failed GitHub Actions runs create repair tasks")
    run_dir = find_run(payload, runs_dir)
    workflow = read_json(run_dir / "workflow.json")
    if workflow.get("execution_status") == "awaiting_approval":
        raise CIIngestionError("run is awaiting approval and cannot accept CI repair")
    try:
        attachment_payload = continuation_attachment_payload(workflow)
    except ValueError as exc:
        raise CIIngestionError(str(exc)) from exc
    external_run_id = str(workflow_run.get("id", ""))
    if not external_run_id:
        raise CIIngestionError("GitHub Actions run id is missing")
    logs = fetch_failed_logs(run_dir, external_run_id, command_runner)
    logs_path = run_dir / "raw-events" / f"ci-{external_run_id}.log"
    logs_path.parent.mkdir(parents=True, exist_ok=True)
    logs_path.write_text(logs, encoding="utf-8")
    publication_path = run_dir / "artifacts" / "publication.json"
    publication = read_json(publication_path) if publication_path.exists() else {}
    fingerprint = hashlib.sha256(logs.encode("utf-8")).hexdigest()
    feedback = {
        "run_id": run_dir.name,
        "provider": "github_actions",
        "external_run_id": external_run_id,
        "event": "workflow_run",
        "conclusion": "failure",
        "head_branch": str(workflow_run.get("head_branch", "")),
        "head_sha": str(workflow_run.get("head_sha", "")),
        "html_url": str(workflow_run.get("html_url", "")),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "logs_path": str(logs_path.relative_to(run_dir)),
        "failure_fingerprint": fingerprint,
        "repair_task_id": 0,
        "existing_pr_url": str(publication.get("pr_url", "")),
        "status": "ingested",
        "errors": [],
    }
    write_feedback(run_dir, feedback)
    workflow["execution_status"] = "resuming"
    workflow["resume_role"] = "ci-repair-agent"
    workflow["ci_feedback_pending"] = True
    write_json_atomic(run_dir / "workflow.json", workflow)
    record = queue.enqueue(
        task_key=f"ci:{run_dir.name}:{external_run_id}:{fingerprint[:12]}",
        payload={
            "task_id": str(workflow.get("task_id", "task")),
            "goal": "Repair failed GitHub Actions checks using run-scoped CI feedback.",
            "project": str(workflow.get("project", "agent_workspace")),
            "repository": str(workflow.get("repository", "")),
            "branch": str(workflow.get("branch", "")),
            "base_branch": str(workflow.get("base_branch", "main")),
            "run_id": run_dir.name,
            "source": "ci",
            "event_id": external_run_id,
            **attachment_payload,
        },
        priority=100,
        max_retries=2,
        run_id=run_dir.name,
    )
    feedback["repair_task_id"] = record.id
    feedback["status"] = "repair_queued"
    write_feedback(run_dir, feedback)
    return feedback, record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    secret = os.environ.get("AGENT_GITHUB_WEBHOOK_SECRET", "")
    try:
        feedback, record = ingest_ci_failure(
            body=args.payload.read_bytes(),
            signature=args.signature,
            secret=secret,
            queue=TaskQueue(args.db),
        )
    except (CIIngestionError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps({"feedback": feedback, "queue_task": record.__dict__}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
