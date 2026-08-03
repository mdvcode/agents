#!/usr/bin/env python3
"""Execute one role request through `codex exec` with sandboxed structured output."""

from __future__ import annotations

import json
import hashlib
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
if str(SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parent))

from runtime_contracts import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    ROOT,
    SCHEMAS,
    blocked_result,
    contract_section,
    load_json,
    resolve_contract_path,
    validate_contract,
)
from check_codex_runtime import configured_codex_base_command  # noqa: E402


def safe_artifact_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def with_exec_subcommand(command: list[str]) -> list[str]:
    if "exec" in command:
        return command
    return command + ["exec"]


def sandbox_for_filesystem_access(access: str) -> str:
    if access in {"task_worktree_write", "workspace_write", "evidence_write"}:
        return "workspace-write"
    return "read-only"


def role_can_write_repository(access: str) -> bool:
    return access in {"task_worktree_write", "workspace_write"}


def git_snapshot(repo: Path) -> str:
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return ""
    digest = hashlib.sha256()
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary"],
        cwd=repo,
        text=False,
        capture_output=True,
        check=False,
    )
    if diff.returncode == 0:
        digest.update(diff.stdout)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if untracked.returncode == 0:
        for relative in sorted(line.strip() for line in untracked.stdout.splitlines() if line.strip()):
            digest.update(relative.encode("utf-8"))
            path = repo / relative
            if path.is_file():
                digest.update(path.read_bytes())
    return digest.hexdigest()


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


def standard_role_result_schema(output_contract: dict[str, Any]) -> dict[str, Any]:
    status_enum = output_contract.get("enums", {}).get(
        "status",
        ["completed", "blocked", "failed", "awaiting_approval"],
    )
    next_action_enum = output_contract.get("enums", {}).get("next_action")
    next_action: dict[str, Any] = {"type": "string"}
    if isinstance(next_action_enum, list) and next_action_enum:
        next_action["enum"] = next_action_enum
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": status_enum},
            "next_action": next_action,
            "summary": {"type": "string"},
            "artifacts_created": {"type": "array", "items": {"type": "string"}},
            "artifacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
            "blockers": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "tokens_used": {"type": "integer"},
        },
        "required": [
            "status",
            "next_action",
            "summary",
            "artifacts_created",
            "artifacts",
            "blockers",
            "warnings",
            "tokens_used",
        ],
        "additionalProperties": False,
    }


def raw_outputs_dir(request: dict[str, Any], manifest: dict[str, Any] | None = None) -> Path:
    if manifest is not None and isinstance(manifest.get("raw_outputs_dir"), str):
        return Path(str(manifest["raw_outputs_dir"]))
    return Path(str(request["artifacts_dir"])).parent / "raw"


def write_standard_schema(
    *,
    request: dict[str, Any],
    manifest: dict[str, Any],
    output_contract: dict[str, Any],
) -> Path:
    role = str(request["role"]).replace("/", "-")
    path = raw_outputs_dir(request, manifest) / f"{role}-output.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(standard_role_result_schema(output_contract), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def context_budget(manifest: dict[str, Any]) -> tuple[int, int]:
    budget = manifest.get("context_budget", {})
    if not isinstance(budget, dict):
        return 120000, 24000
    max_total = budget.get("max_total_bytes", 120000)
    max_file = budget.get("max_file_bytes", 24000)
    return (
        max_total if isinstance(max_total, int) and max_total > 0 else 120000,
        max_file if isinstance(max_file, int) and max_file > 0 else 24000,
    )


def context_reference_contents(manifest: dict[str, Any]) -> str:
    max_total_bytes, max_bytes_per_file = context_budget(manifest)
    package_path = manifest.get("context_package_path")
    if isinstance(package_path, str) and package_path:
        path = Path(package_path)
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"### context_package: {path}\n[unavailable: {exc}]"
        encoded = content.encode("utf-8")
        if len(encoded) > max_total_bytes:
            content = encoded[:max_total_bytes].decode("utf-8", errors="ignore").rstrip()
            content += "\n[truncated: context byte safety limit]"
        return f"### context_package: {path}\n{content}"
    references: list[tuple[str, str]] = []
    for item in manifest.get("context_files", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            references.append((str(item.get("kind", "context")), str(item["path"])))
    for item in manifest.get("skill_references", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            references.append((f"skill:{item.get('name', 'skill')}", str(item["path"])))
    for item in manifest.get("artifact_references", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            references.append((f"artifact:{item.get('kind', 'file')}", str(item["path"])))
    chunks: list[str] = []
    seen: set[str] = set()
    excluded = {str(item) for item in manifest.get("excluded_context", []) if isinstance(item, str)}
    total_bytes = 0
    for kind, path_value in references:
        path = Path(path_value)
        key = str(path)
        if key in seen or key in excluded or not path.is_file():
            continue
        seen.add(key)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            chunks.append(f"### {kind}: {path}\n[unavailable: {exc}]")
            continue
        encoded = content.encode("utf-8")
        if len(encoded) > max_bytes_per_file:
            content = content[:max_bytes_per_file] + "\n[truncated]"
            encoded = content.encode("utf-8")
        if total_bytes + len(encoded) > max_total_bytes:
            remaining = max_total_bytes - total_bytes
            if remaining <= 0:
                chunks.append("[context budget exhausted]")
                break
            content = content[:remaining] + "\n[truncated: context budget exhausted]"
            encoded = content.encode("utf-8")
        chunks.append(f"### {kind}: {path}\n{content}")
        total_bytes += len(encoded)
    return "\n\n".join(chunks)


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
            "Context file contents available to this sandboxed run:",
            context_reference_contents(manifest),
            "Required JSON response schema:",
            json.dumps(standard_role_result_schema(output_contract), indent=2, ensure_ascii=False),
            (
                "Return only one JSON object matching the schema. Put non-code artifacts in the "
                "`artifacts` array as {path, content}; the harness writes them to artifacts_dir."
            ),
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
    result.setdefault("artifacts", [])
    errors = validate_contract(result, output_contract, "role_result")
    if errors:
        return blocked_result("Codex CLI result failed schema validation.", errors)
    return result


def parse_jsonl_events(raw_stdout: str) -> tuple[str, dict[str, int], list[str]]:
    thread_id = ""
    usage = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    errors: list[str] = []
    for line in raw_stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = str(event.get("thread_id", ""))
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            event_usage = event["usage"]
            for field in usage:
                value = event_usage.get(field)
                if isinstance(value, int):
                    usage[field] = value
        if event.get("type") in {"turn.failed", "error"}:
            errors.append(json.dumps(event, ensure_ascii=False))
    return thread_id, usage, errors


def write_raw_stream(request: dict[str, Any], manifest: dict[str, Any], raw_stdout: str) -> Path:
    role = str(request["role"]).replace("/", "-")
    path = raw_outputs_dir(request, manifest) / f"{role}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_stdout, encoding="utf-8")
    return path


def role_result_output_path(request: dict[str, Any], manifest: dict[str, Any]) -> Path:
    role = str(request["role"]).replace("/", "-")
    path = raw_outputs_dir(request, manifest) / f"{role}-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_artifacts_from_result(request: dict[str, Any], result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    artifacts_dir = Path(str(request["artifacts_dir"]))
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    created = list(result.get("artifacts_created", []))
    artifacts = result.get("artifacts", [])
    allowed = {str(path) for path in request.get("expected_artifacts", []) if isinstance(path, str)}
    if not isinstance(artifacts, list):
        return ["role_result.artifacts must be a list"]
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("role_result.artifacts entries must be objects")
            continue
        path_value = artifact.get("path")
        content = artifact.get("content")
        if not safe_artifact_name(path_value):
            errors.append(f"artifact path is unsafe: {path_value!r}")
            continue
        if str(path_value) not in allowed:
            errors.append(
                "Role attempted to write an artifact it does not own: "
                f"{request['role']} cannot write {path_value}"
            )
            continue
        if not isinstance(content, str):
            errors.append(f"artifact {path_value!r} content must be a string")
            continue
        artifact_path = artifacts_dir / str(path_value)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")
        if path_value not in created:
            created.append(str(path_value))
    result["artifacts_created"] = created
    return errors


def validate_artifact_ownership(request: dict[str, Any], result: dict[str, Any]) -> list[str]:
    allowed = {str(path) for path in request.get("expected_artifacts", []) if isinstance(path, str)}
    errors: list[str] = []
    for path_value in result.get("artifacts_created", []):
        if not safe_artifact_name(path_value):
            errors.append(f"artifacts_created contains unsafe path {path_value!r}")
            continue
        if str(path_value) not in allowed:
            errors.append(
                "Role attempted to publish an artifact it does not own: "
                f"{request['role']} cannot claim {path_value}"
            )
    return errors


def write_deterministic_artifacts(request: dict[str, Any], result: dict[str, Any]) -> None:
    artifacts_dir = Path(str(request["artifacts_dir"]))
    expected = request.get("expected_artifacts", [])
    created = list(result.get("artifacts_created", []))
    if "project_profile.json" in expected:
        path = artifacts_dir / "project_profile.json"
        if not path.exists():
            payload = {
                "project_profile": str(request.get("project_profile", "")),
                "confidence": "high",
                "reasons": ["Selected by the workflow project profile resolver."],
                "matched_markers": [],
                "quality_commands_selected": [],
                "security_commands_selected": [],
                "frontend_evidence_required": False,
                "warnings": [],
            }
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if "project_profile.json" not in created:
            created.append("project_profile.json")
    result["artifacts_created"] = created


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
    manifest: dict[str, Any],
) -> dict[str, Any]:
    schema_path = write_standard_schema(request=request, manifest=manifest, output_contract=output_contract)
    result_path = role_result_output_path(request, manifest)
    command = with_exec_subcommand(configured_codex_base_command()) + [
        "--json",
        "--sandbox",
        sandbox_for_filesystem_access(str(request.get("filesystem_access", "read_only"))),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(result_path),
        "-",
    ]
    if str(request.get("filesystem_access", "")) == "evidence_write":
        command[command.index("-"):command.index("-")] = [
            "--add-dir",
            str(Path(str(request["artifacts_dir"])).resolve()),
        ]
    repository = Path(str(request["repository"]))
    env = os.environ.copy()
    env["AGENT_ROLE"] = str(request["role"])
    env["AGENT_ROLE_ALLOWED_TOOLS"] = json.dumps(request.get("allowed_tools", []))
    env["AGENT_ROLE_FILESYSTEM_ACCESS"] = str(request.get("filesystem_access", "read_only"))
    env["AGENT_TOOL_POLICY_PATH"] = str((Path(__file__).resolve().parents[2] / ".agent-tool-policy.yaml"))
    before_snapshot = ""
    if not role_can_write_repository(str(request.get("filesystem_access", "read_only"))):
        before_snapshot = git_snapshot(repository)
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
    write_raw_stream(request, manifest, completed.stdout)
    thread_id, usage, event_errors = parse_jsonl_events(completed.stdout)
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip()
        return blocked_result("Codex CLI command failed.", [output or f"exit {completed.returncode}"])
    if event_errors:
        return blocked_result("Codex CLI emitted failure events.", event_errors)
    if before_snapshot and git_snapshot(repository) != before_snapshot:
        return blocked_result(
            "Read-only role changed repository contents.",
            ["git snapshot changed while sandbox was expected to be read-only"],
        )
    try:
        result_stdout = result_path.read_text(encoding="utf-8")
    except OSError:
        result_stdout = completed.stdout
    result = parse_role_result(result_stdout, output_contract, duration_ms)
    if thread_id:
        result["thread_id"] = thread_id
    result["input_tokens"] = usage["input_tokens"]
    result["cached_input_tokens"] = usage["cached_input_tokens"]
    result["output_tokens"] = usage["output_tokens"]
    result["reasoning_output_tokens"] = usage["reasoning_output_tokens"]
    uncached_input = max(usage["input_tokens"] - usage["cached_input_tokens"], 0)
    usage_total = uncached_input + usage["output_tokens"]
    if usage_total:
        result["tokens_used"] = usage_total
    artifact_errors = write_artifacts_from_result(request, result)
    write_deterministic_artifacts(request, result)
    artifact_errors.extend(validate_artifact_ownership(request, result))
    if artifact_errors:
        return blocked_result("Codex CLI returned invalid artifacts.", artifact_errors)
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
        manifest=manifest,
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
