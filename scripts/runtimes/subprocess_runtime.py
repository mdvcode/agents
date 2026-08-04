"""Generic structured subprocess runtime used by provider adapters and test fixtures."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        started_monotonic = time.monotonic()
        effective_timeout = int(task.get("timeout_seconds", self.timeout_seconds) or self.timeout_seconds)
        try:
            completed = subprocess.run(
                shlex.split(self.descriptor.command),
                input=payload,
                text=True,
                capture_output=True,
                check=False,
                timeout=effective_timeout,
            )
        except FileNotFoundError as exc:
            return recovery_blocked("Runtime adapter command is missing.", [str(exc)], kind="tool_failure", error_type="FileNotFoundError")
        except subprocess.TimeoutExpired as exc:
            self._write_raw(task, started, 124, exc.stdout or "", exc.stderr or "")
            return recovery_blocked(
                "Runtime adapter command timed out.",
                [f"timeout after {effective_timeout} seconds"],
                kind="transient",
                error_type="TimeoutExpired",
            )
        except PermissionError as exc:
            return recovery_blocked("Runtime adapter command is not executable.", [str(exc)], kind="tool_failure", error_type="PermissionError")

        self._write_raw(task, started, completed.returncode, completed.stdout, completed.stderr)
        duration_ms = int((time.monotonic() - started_monotonic) * 1000)
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
