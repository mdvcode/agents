#!/usr/bin/env python3
"""Execute one role request through a Codex CLI-compatible command."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from codex_adapter import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    ROOT,
    SCHEMAS,
    blocked_result,
    contract_section,
    load_json,
    resolve_contract_path,
    validate_contract,
)


def safe_artifact_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def configured_codex_command() -> list[str]:
    command = os.environ.get("AGENT_CODEX_CLI_COMMAND", "codex")
    args = os.environ.get("AGENT_CODEX_CLI_ARGS", "")
    return shlex.split(command) + shlex.split(args)


def read_request() -> tuple[dict[str, Any] | None, list[str]]:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        return None, [f"stdin: malformed role request JSON: {exc.msg} at line {exc.lineno}"]
    if not isinstance(request, dict):
        return None, ["stdin: role request must be a JSON object"]
    errors = validate_contract(request, load_json(SCHEMAS / "role_request.schema.json"), "role_request")
    return request, errors


def read_text_file(path_value: str, label: str) -> tuple[str, list[str]]:
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT / path
    try:
        return path.read_text(encoding="utf-8"), []
    except OSError as exc:
        return "", [f"{label}: {exc}"]


def read_context_manifest(path_value: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        manifest = load_json(Path(path_value))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, [f"context_manifest: {exc}"]
    errors = validate_contract(
        manifest,
        load_json(SCHEMAS / "context_manifest.schema.json"),
        "context_manifest",
    )
    return manifest, errors


def read_output_contract(path_value: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        schema = load_json(resolve_contract_path(path_value))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, [f"output_contract: {exc}"]
    return contract_section(schema, "role_result"), []


def role_prompt_payload(
    *,
    request: dict[str, Any],
    prompt_text: str,
    manifest: dict[str, Any],
    output_contract: dict[str, Any],
) -> str:
    return "\n\n".join(
        [
            prompt_text,
            "Role execution request:",
            json.dumps(request, indent=2, ensure_ascii=False),
            "Context manifest:",
            json.dumps(manifest, indent=2, ensure_ascii=False),
            "Required JSON response schema:",
            json.dumps(output_contract, indent=2, ensure_ascii=False),
            "Return only one JSON object matching the required role result contract.",
        ]
    )


def parse_role_result(stdout: str, output_contract: dict[str, Any], duration_ms: int) -> dict[str, Any]:
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return blocked_result("Codex CLI returned malformed JSON.", [f"{exc.msg} at line {exc.lineno}"])
    if not isinstance(result, dict):
        return blocked_result("Codex CLI returned a non-object JSON value.", ["role result must be an object"])
    result.setdefault("duration_ms", duration_ms)
    errors = validate_contract(result, output_contract, "role_result")
    if errors:
        return blocked_result("Codex CLI result failed schema validation.", errors)
    return result


def validate_expected_artifacts(request: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    artifacts_dir = Path(str(request["artifacts_dir"]))
    for artifact in request.get("expected_artifacts", []):
        if not safe_artifact_name(artifact):
            errors.append(f"expected_artifacts contains unsafe path {artifact!r}")
            continue
        artifact_path = artifacts_dir / artifact
        if not artifact_path.exists():
            errors.append(f"role must create run-scoped {artifact}")
            continue
        if artifact_path.is_file() and artifact_path.stat().st_size == 0:
            errors.append(f"role must create non-empty run-scoped {artifact}")
    return errors


def run_codex(
    *,
    request: dict[str, Any],
    prompt: str,
    timeout_seconds: int,
    output_contract: dict[str, Any],
) -> dict[str, Any]:
    command = configured_codex_command()
    repository = Path(str(request["repository"]))
    env = os.environ.copy()
    env["AGENT_ROLE"] = str(request["role"])
    env["AGENT_ROLE_ALLOWED_TOOLS"] = json.dumps(request.get("allowed_tools", []))
    env["AGENT_ROLE_FILESYSTEM_ACCESS"] = str(request.get("filesystem_access", "read_only"))
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
        )
    except FileNotFoundError as exc:
        return blocked_result("Codex CLI command is missing.", [str(exc)])
    except PermissionError as exc:
        return blocked_result("Codex CLI command is not executable.", [str(exc)])
    except subprocess.TimeoutExpired:
        return blocked_result("Codex CLI command timed out.", [f"timeout after {timeout_seconds} seconds"])
    duration_ms = int((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip()
        return blocked_result("Codex CLI command failed.", [output or f"exit {completed.returncode}"])
    result = parse_role_result(completed.stdout, output_contract, duration_ms)
    if result.get("status") == "completed":
        artifact_errors = validate_expected_artifacts(request)
        if artifact_errors:
            return blocked_result("Codex CLI completed without required artifacts.", artifact_errors)
    return result


def execute_role() -> dict[str, Any]:
    request, request_errors = read_request()
    if request is None:
        return blocked_result("Role request could not be read.", request_errors)
    if request_errors:
        return blocked_result("Role request failed schema validation.", request_errors)

    prompt_text, prompt_errors = read_text_file(str(request["prompt_path"]), "prompt_path")
    manifest, manifest_errors = read_context_manifest(str(request["context_manifest"]))
    output_contract, contract_errors = read_output_contract(str(request["output_contract"]))
    errors = prompt_errors + manifest_errors + contract_errors
    if errors:
        return blocked_result("Role execution inputs could not be loaded.", errors)
    assert manifest is not None
    assert output_contract is not None

    prompt = role_prompt_payload(
        request=request,
        prompt_text=prompt_text,
        manifest=manifest,
        output_contract=output_contract,
    )
    timeout_seconds = int(request.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)
    return run_codex(
        request=request,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
        output_contract=output_contract,
    )


def main() -> int:
    result = execute_role()
    result.setdefault("warnings", [])
    result.setdefault("blockers", [])
    result.setdefault("artifacts_created", [])
    result.setdefault("tokens_used", 0)
    result.setdefault("duration_ms", 0)
    result.setdefault("summary", "Role execution finished.")
    result.setdefault("next_action", "blocked" if result.get("status") == "blocked" else "continue")
    result.setdefault("status", "blocked")
    result.setdefault("finished_at", datetime.now(timezone.utc).isoformat())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
