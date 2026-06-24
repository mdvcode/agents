#!/usr/bin/env python3
"""Codex role adapter with strict request/result contracts."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
DEFAULT_TIMEOUT_SECONDS = 600


class ContractError(ValueError):
    """Raised when a role request or result does not match its schema."""


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ContractError(f"{path.name}: top-level value must be an object")
    return data


def validate_contract(data: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    type_map = {
        "str": str,
        "bool": bool,
        "list": list,
        "dict": dict,
        "int": int,
    }
    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"{label}: missing required field {field!r}")
    for field, type_name in schema.get("types", {}).items():
        if field not in data:
            continue
        expected = type_map.get(type_name)
        if expected is None:
            errors.append(f"{label}: schema uses unknown type {type_name!r} for {field!r}")
            continue
        if not isinstance(data[field], expected):
            errors.append(
                f"{label}: field {field!r} must be {type_name}, got {type(data[field]).__name__}"
            )
    for field, allowed in schema.get("enums", {}).items():
        if field in data and data[field] not in allowed:
            errors.append(f"{label}: field {field!r} has invalid value {data[field]!r}")
    return errors


def contract_section(schema: dict[str, Any], section: str) -> dict[str, Any]:
    selected = schema.get(section)
    if isinstance(selected, dict):
        return selected
    return schema


def resolve_contract_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return ROOT / path


def configured_command(command: str = "") -> str:
    return command or os.environ.get("AGENT_CODEX_COMMAND", "") or os.environ.get("AGENT_LLM_COMMAND", "")


def blocked_result(summary: str, blockers: Sequence[str], warnings: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "status": "blocked",
        "next_action": "blocked",
        "summary": summary,
        "artifacts_created": [],
        "blockers": list(blockers),
        "warnings": list(warnings),
        "tokens_used": 0,
    }


@dataclass
class CodexAdapter:
    """Execute one role through a configured external Codex-compatible command."""

    command: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    raw_output_dir: Path | None = None

    def invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        request_schema = load_json(SCHEMAS / "role_request.schema.json")
        result_schema = load_json(SCHEMAS / "role_result.schema.json")
        request_errors = validate_contract(request, request_schema, "role_request")
        if request_errors:
            return blocked_result("Role request failed schema validation.", request_errors)

        command = configured_command(self.command)
        if not command:
            return blocked_result(
                "No Codex adapter command configured.",
                ["Set AGENT_CODEX_COMMAND or pass --adapter-command to execute roles."],
            )

        payload = json.dumps(request, ensure_ascii=False)
        started = datetime.now(timezone.utc).isoformat()
        started_monotonic = time.monotonic()
        try:
            completed = subprocess.run(
                shlex.split(command),
                input=payload,
                text=True,
                capture_output=True,
                check=False,
                timeout=int(request.get("timeout_seconds", self.timeout_seconds) or self.timeout_seconds),
            )
        except FileNotFoundError as exc:
            return blocked_result("Codex adapter command is missing.", [str(exc)])
        except subprocess.TimeoutExpired as exc:
            self._write_raw(request, started, 124, exc.stdout or "", exc.stderr or "")
            return blocked_result(
                "Codex adapter command timed out.",
                [f"timeout after {request.get('timeout_seconds', self.timeout_seconds)} seconds"],
            )
        except PermissionError as exc:
            return blocked_result("Codex adapter command is not executable.", [str(exc)])

        self._write_raw(request, started, completed.returncode, completed.stdout, completed.stderr)
        duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout).strip()
            return blocked_result("Codex adapter command failed.", [output or f"exit {completed.returncode}"])
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return blocked_result("Codex adapter returned malformed JSON.", [f"{exc.msg} at line {exc.lineno}"])
        if not isinstance(result, dict):
            return blocked_result("Codex adapter returned a non-object JSON value.", ["role result must be an object"])
        result.setdefault("duration_ms", duration_ms)
        output_contract = request.get("output_contract")
        if isinstance(output_contract, str) and output_contract:
            try:
                result_schema = contract_section(load_json(resolve_contract_path(output_contract)), "role_result")
            except (OSError, json.JSONDecodeError, ContractError) as exc:
                return blocked_result("Role output contract could not be loaded.", [str(exc)])
        result_errors = validate_contract(result, result_schema, "role_result")
        if result_errors:
            return blocked_result("Codex adapter result failed schema validation.", result_errors)
        return result

    def _write_raw(
        self,
        request: dict[str, Any],
        started: str,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        if self.raw_output_dir is None:
            return
        self.raw_output_dir.mkdir(parents=True, exist_ok=True)
        role = str(request.get("role", "role")).replace("/", "-")
        payload = {
            "time": started,
            "role": role,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        (self.raw_output_dir / f"{role}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
