"""Generic structured subprocess runtime used by provider adapters and test fixtures."""

from __future__ import annotations

import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HARNESS_ROOT = Path(__file__).resolve().parents[2]
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

from ai_harness.processes import run_managed_process
from ai_harness.recovery.policy import load_recovery_policy
from runtime_contracts import (
    ContractError,
    SCHEMAS,
    blocked_result,
    contract_section,
    load_json,
    resolve_contract_path,
    validate_contract,
)
from runtimes.base import RuntimeDescriptor


def recovery_blocked(summary: str, blockers: list[str], *, kind: str, error_type: str) -> dict[str, Any]:
    result = blocked_result(summary, blockers)
    result["_failure"] = {"kind": kind, "error_type": error_type, "message": summary}
    return result


class SubprocessRuntime:
    def __init__(
        self,
        *,
        descriptor: RuntimeDescriptor,
        timeout_seconds: int = 600,
        raw_output_dir: Path | None = None,
    ) -> None:
        self._descriptor = descriptor
        self.timeout_seconds = timeout_seconds
        self.raw_output_dir = raw_output_dir

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return self._descriptor

    def preflight(self, *, worktree: Path, timeout_seconds: int) -> dict[str, Any]:
        if not self.descriptor.command:
            return {"execution_status": "blocked", "blockers": ["runtime command is missing"], "warnings": []}
        return {"execution_status": "completed", "blockers": [], "warnings": []}

    def execute(
        self,
        *,
        role: str,
        context: Path,
        task: dict[str, Any],
        worktree: Path,
        artifacts: Path,
    ) -> dict[str, Any]:
        boundary_errors = self._boundary_errors(role, context, task, worktree, artifacts)
        if boundary_errors:
            return blocked_result("Runtime invocation boundary is invalid.", boundary_errors)
        request_errors = validate_contract(task, load_json(SCHEMAS / "role_request.schema.json"), "role_request")
        if request_errors:
            return blocked_result("Role request failed schema validation.", request_errors)
        if not self.descriptor.command:
            return blocked_result("Runtime adapter command is not configured.", ["runtime command is empty"])

        payload = json.dumps(task, ensure_ascii=False)
        started = datetime.now(timezone.utc).isoformat()
        limits = load_recovery_policy().runtime_limits
        requested_timeout = int(task.get("timeout_seconds", self.timeout_seconds) or self.timeout_seconds)
        effective_timeout = min(requested_timeout, limits.role_timeout_seconds)
        safe_role = role.replace("/", "-")
        raw_dir = self.raw_output_dir or artifacts.parent / "raw-events"
        command = shlex.split(self.descriptor.command)
        if command and command[0] == "python3":
            command[0] = sys.executable
        try:
            completed = run_managed_process(
                command,
                cwd=HARNESS_ROOT,
                input_text=payload,
                stdout_path=raw_dir / f"runtime.{safe_role}.stdout.log",
                stderr_path=raw_dir / f"runtime.{safe_role}.stderr.log",
                timeout_seconds=effective_timeout,
                idle_timeout_seconds=min(limits.idle_timeout_seconds, effective_timeout),
                shutdown_grace_seconds=limits.shutdown_grace_seconds,
                max_output_bytes=limits.max_output_bytes,
                artifact_paths=(artifacts,),
                progress_paths=(artifacts.parent / "progress.json",),
                max_artifact_bytes=limits.max_artifact_bytes,
                max_open_files=limits.max_open_files,
            )
        except FileNotFoundError as exc:
            return recovery_blocked("Runtime adapter command is missing.", [str(exc)], kind="tool_failure", error_type="FileNotFoundError")
        except PermissionError as exc:
            return recovery_blocked("Runtime adapter command is not executable.", [str(exc)], kind="tool_failure", error_type="PermissionError")

        self._write_raw(task, started, completed.returncode, completed.stdout, completed.stderr)
        duration_ms = int(completed.duration_seconds * 1000)
        if completed.timed_out or completed.idle_timed_out:
            error_type = "IdleTimeout" if completed.idle_timed_out else "TimeoutExpired"
            timeout_kind = "idle" if completed.idle_timed_out else "runtime"
            return recovery_blocked(
                "Runtime adapter command timed out.",
                [f"{timeout_kind} timeout after {effective_timeout} seconds"],
                kind="transient",
                error_type=error_type,
            )
        if completed.output_limit_exceeded:
            return recovery_blocked(
                "Runtime adapter output exceeded the configured limit.",
                [f"output limit is {limits.max_output_bytes} bytes"],
                kind="runtime_failure",
                error_type="OutputLimitExceeded",
            )
        if completed.artifact_limit_exceeded:
            return recovery_blocked(
                "Runtime artifacts exceeded the configured limit.",
                [f"artifact limit is {limits.max_artifact_bytes} bytes"],
                kind="runtime_failure",
                error_type="ArtifactLimitExceeded",
            )
        if completed.open_file_limit_exceeded:
            return recovery_blocked(
                "Runtime open-file budget is exhausted.",
                [f"open-file limit is {limits.max_open_files}"],
                kind="runtime_failure",
                error_type="OpenFileLimitExceeded",
            )
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout).strip()
            return recovery_blocked(
                "Runtime adapter command failed.",
                [output or f"exit {completed.returncode}"],
                kind="runtime_failure",
                error_type="RuntimeProcessFailure",
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return recovery_blocked(
                "Runtime adapter returned malformed JSON.",
                [f"{exc.msg} at line {exc.lineno}"],
                kind="invalid_output",
                error_type="InvalidStructuredOutput",
            )
        if not isinstance(result, dict):
            return recovery_blocked(
                "Runtime adapter returned a non-object JSON value.",
                ["role result must be an object"],
                kind="invalid_output",
                error_type="InvalidStructuredOutput",
            )
        result.setdefault("duration_ms", duration_ms)
        result_schema = load_json(SCHEMAS / "role_result.schema.json")
        output_contract = task.get("output_contract")
        if isinstance(output_contract, str) and output_contract:
            try:
                result_schema = contract_section(load_json(resolve_contract_path(output_contract)), "role_result")
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                return blocked_result("Role output contract could not be loaded.", [str(exc)])
        errors = validate_contract(result, result_schema, "role_result")
        return (
            recovery_blocked(
                "Runtime adapter result failed schema validation.",
                errors,
                kind="invalid_output",
                error_type="InvalidStructuredOutput",
            )
            if errors
            else result
        )

    @staticmethod
    def _boundary_errors(
        role: str,
        context: Path,
        task: dict[str, Any],
        worktree: Path,
        artifacts: Path,
    ) -> list[str]:
        expected = {
            "role": role,
            "context_manifest": str(context.resolve()),
            "repository": str(worktree.resolve()),
            "artifacts_dir": str(artifacts.resolve()),
        }
        return [
            f"runtime task {field} does not match invocation boundary"
            for field, value in expected.items()
            if str(task.get(field, "")) != value
        ]

    def _write_raw(
        self,
        task: dict[str, Any],
        started: str,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        if self.raw_output_dir is None:
            return
        self.raw_output_dir.mkdir(parents=True, exist_ok=True)
        role = str(task.get("role", "role")).replace("/", "-")
        payload = {
            "time": started,
            "provider": self.descriptor.provider,
            "role": role,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        (self.raw_output_dir / f"{role}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
