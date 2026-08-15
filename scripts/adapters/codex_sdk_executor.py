#!/usr/bin/env python3
"""Execute one role through the official Python Codex SDK."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR.parent, SCRIPT_DIR.parents[1]):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from codex_cli_executor import (  # noqa: E402
    MAX_OUTPUT_REPAIR_ATTEMPTS,
    failure_result,
    git_snapshot,
    parse_role_result,
    read_context_manifest,
    read_output_contract,
    read_request,
    read_text_file,
    role_can_write_repository,
    role_prompt_payload,
    standard_role_result_schema,
    validate_artifact_ownership,
    validate_expected_artifacts,
    write_artifacts_from_result,
    write_deterministic_artifacts,
    write_raw_stream,
)
from runtime_contracts import blocked_result  # noqa: E402


ROOT = SCRIPT_DIR.parents[1]


def sdk_settings() -> dict[str, str]:
    try:
        document = yaml.safe_load((ROOT / ".agent-runtime.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        document = {}
    runtime = document.get("runtime", {}) if isinstance(document, dict) else {}
    if not isinstance(runtime, dict):
        runtime = {}
    return {
        "model": os.environ.get("AGENT_CODEX_MODEL", str(runtime.get("model", "gpt-5.6-sol"))),
        "reasoning_effort": os.environ.get(
            "AGENT_CODEX_REASONING_EFFORT", str(runtime.get("reasoning_effort", "high"))
        ),
        "service_tier": os.environ.get(
            "AGENT_CODEX_SERVICE_TIER", str(runtime.get("service_tier", "fast"))
        ),
    }


def sdk_sandbox(filesystem_access: str) -> Any:
    from openai_codex import Sandbox

    if filesystem_access in {"task_worktree_write", "workspace_write", "evidence_write"}:
        return Sandbox.workspace_write
    return Sandbox.read_only


def usage_fields(turn_result: Any) -> dict[str, int]:
    usage = getattr(turn_result, "usage", None)
    breakdown = getattr(usage, "last", None)
    fields = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    for name in fields:
        value = getattr(breakdown, name, 0)
        if isinstance(value, int):
            fields[name] = value
    return fields


def validate_candidate(request: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    invalid_summaries = {
        "Codex SDK returned malformed JSON.",
        "Codex SDK returned a non-object JSON value.",
        "Codex SDK result failed schema validation.",
    }
    if candidate.get("summary") in invalid_summaries:
        return [str(item) for item in candidate.get("blockers", [])]
    errors = write_artifacts_from_result(request, candidate)
    write_deterministic_artifacts(request, candidate)
    errors.extend(validate_artifact_ownership(request, candidate))
    if candidate.get("status") == "completed":
        errors.extend(validate_expected_artifacts(request))
    return errors


def run_sdk(
    *,
    request: dict[str, Any],
    prompt: str,
    output_contract: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    try:
        from openai_codex import ApprovalMode, Codex, CodexConfig
    except ImportError as exc:
        return failure_result(
            "Official Python Codex SDK is unavailable.",
            [str(exc)],
            kind="tool_failure",
            error_type="SdkUnavailable",
        )

    repository = Path(str(request["repository"])).resolve()
    filesystem_access = str(request.get("filesystem_access", "read_only"))
    schema = standard_role_result_schema(output_contract)
    settings = sdk_settings()
    environment = {
        "AGENT_ROLE": str(request["role"]),
        "AGENT_ROLE_ALLOWED_TOOLS": json.dumps(request.get("allowed_tools", [])),
        "AGENT_ROLE_FILESYSTEM_ACCESS": filesystem_access,
        "AGENT_TOOL_POLICY_PATH": str(ROOT / ".agent-tool-policy.yaml"),
    }
    config_overrides = ["features.fast_mode=true"]
    if filesystem_access == "evidence_write":
        writable_roots = [
            str(Path(str(request["artifacts_dir"])).resolve()),
            str((Path(str(request["artifacts_dir"])).resolve().parent / "tmp" / str(request["role"])).resolve()),
        ]
        config_overrides.append(
            "sandbox_workspace_write.writable_roots=" + json.dumps(writable_roots)
        )

    before_snapshot = ""
    if not role_can_write_repository(filesystem_access):
        before_snapshot = git_snapshot(repository)
    started = time.monotonic()
    try:
        with Codex(
            CodexConfig(
                cwd=str(repository),
                env=environment,
                config_overrides=tuple(config_overrides),
                client_name="ai_harness",
                client_title="AI Harness",
            )
        ) as codex:
            account = codex.account().account
            account_root = account.root if account is not None else None
            if str(getattr(account_root, "type", "")) != "chatgpt":
                return failure_result(
                    "ChatGPT subscription authentication is required.",
                    ["Sign in to Codex with ChatGPT; API-key sessions are not accepted by this runtime."],
                    kind="policy_block",
                    error_type="SubscriptionAuthRequired",
                )
            thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=str(repository),
                ephemeral=True,
                model=settings["model"],
                sandbox=sdk_sandbox(filesystem_access),
                service_name="ai-harness",
                service_tier=settings["service_tier"],
            )
            turn_result = thread.run(
                prompt,
                effort=settings["reasoning_effort"],
                output_schema=schema,
                sandbox=sdk_sandbox(filesystem_access),
                service_tier=settings["service_tier"],
            )
            response = turn_result.final_response or ""
            usage = usage_fields(turn_result)
            thread_id = thread.id

            duration_ms = int((time.monotonic() - started) * 1000)
            write_raw_stream(
                request,
                manifest,
                json.dumps(
                    {
                        "provider": "codex-sdk",
                        "thread_id": thread_id,
                        "turn_id": turn_result.id,
                        "status": getattr(turn_result.status, "value", str(turn_result.status)),
                        "duration_ms": duration_ms,
                        "usage": usage,
                        "final_response": response,
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
            result = parse_role_result(response, output_contract, duration_ms, "Codex SDK")
            errors = validate_candidate(request, result)
            original_output = response
            for repair_attempt in range(1, MAX_OUTPUT_REPAIR_ATTEMPTS + 1):
                if not errors:
                    break
                repair_prompt = "\n".join(
                    [
                        "Repair this structured output without doing task work.",
                        "Original structured output:",
                        original_output,
                        "Required schema:",
                        json.dumps(schema, ensure_ascii=False, sort_keys=True),
                        "Validation errors:",
                        json.dumps(errors, ensure_ascii=False),
                    ]
                )
                repaired_turn = thread.run(
                    repair_prompt,
                    effort=settings["reasoning_effort"],
                    output_schema=schema,
                    sandbox=sdk_sandbox("read_only"),
                    service_tier=settings["service_tier"],
                )
                repaired_output = repaired_turn.final_response or ""
                write_raw_stream(
                    request,
                    manifest,
                    json.dumps(
                        {
                            "provider": "codex-sdk",
                            "thread_id": thread_id,
                            "turn_id": repaired_turn.id,
                            "final_response": repaired_output,
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    suffix=f"-repair-{repair_attempt}",
                )
                candidate = parse_role_result(
                    repaired_output, output_contract, duration_ms, "Codex SDK"
                )
                candidate_errors = validate_candidate(request, candidate)
                if not candidate_errors:
                    result = candidate
                    warnings = list(result.get("warnings", []))
                    warnings.append(
                        f"Structured output repaired after {repair_attempt} validation-only attempt(s)."
                    )
                    result["warnings"] = warnings
                    result["output_repair_attempts"] = repair_attempt
                    errors = []
                    repair_usage = usage_fields(repaired_turn)
                    for name, value in repair_usage.items():
                        usage[name] += value
                    break
                errors = candidate_errors
            if errors:
                result = failure_result(
                    "Structured output repair budget exhausted.",
                    errors,
                    kind="invalid_output",
                    error_type="InvalidStructuredOutput",
                )
                result["_failure"]["repair_attempts"] = MAX_OUTPUT_REPAIR_ATTEMPTS
    except Exception as exc:
        return failure_result(
            "Codex SDK execution failed.",
            [f"{type(exc).__name__}: {exc}"],
            kind="runtime_failure",
            error_type="SdkExecutionError",
        )

    if before_snapshot and git_snapshot(repository) != before_snapshot:
        return failure_result(
            "Read-only role changed repository contents.",
            ["git snapshot changed while sandbox was expected to be read-only"],
            kind="policy_block",
            error_type="ReadOnlyViolation",
        )
    result["thread_id"] = thread_id
    for name, value in usage.items():
        result[name] = value
    billable_tokens = max(0, usage["input_tokens"] - usage["cached_input_tokens"]) + usage["output_tokens"]
    if billable_tokens:
        result["tokens_used"] = billable_tokens
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
    return run_sdk(
        request=request,
        prompt=role_prompt_payload(
            request=request,
            prompt_text=prompt_text,
            manifest=manifest,
            output_contract=output_contract,
        ),
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
