#!/usr/bin/env python3
"""Execute one role through the official Python Codex SDK."""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
import time
from contextlib import suppress
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


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
from ai_harness.context.content_guard import ContextGuardError  # noqa: E402
from ai_harness.context.payload import record_payload  # noqa: E402
from ai_harness.model_policy import (  # noqa: E402
    ModelPolicyError,
    load_execution_profiles,
    validate_request_profile,
)


ROOT = SCRIPT_DIR.parents[1]
SESSION_SOCKET_ENV = "AGENT_CODEX_SDK_SESSION_SOCKET"
MAX_SESSION_MESSAGE_BYTES = 64 * 1024 * 1024


class SessionDisconnected(RuntimeError):
    """Raised when the bounded role client disappears during an SDK turn."""


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if is_dataclass(value):
        return json_value(asdict(value))
    for method_name in ("model_dump", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            with suppress(Exception):
                return json_value(method())
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict):
        return json_value(
            {key: item for key, item in raw.items() if not key.startswith("_")}
        )
    return str(value)


def sdk_event_payload(event: Any) -> dict[str, Any]:
    raw = json_value(getattr(event, "payload", None))
    compact: dict[str, Any] = {}
    if isinstance(raw, dict):
        for key in ("thread_id", "threadId", "turn_id", "turnId", "status"):
            value = raw.get(key)
            if isinstance(value, (str, int, bool)):
                compact[key] = value
        item = raw.get("item")
        if isinstance(item, dict):
            compact_item = {
                key: item[key]
                for key in ("id", "type", "name", "tool_name", "toolName", "server")
                if isinstance(item.get(key), (str, int, bool))
            }
            if compact_item:
                compact["item"] = compact_item
        usage = raw.get("token_usage", raw.get("tokenUsage"))
        if isinstance(usage, dict) and isinstance(usage.get("total"), dict):
            total = usage["total"]
            compact["token_usage"] = {
                "total": {
                    key: total[key]
                    for key in ("total_tokens", "totalTokens")
                    if isinstance(total.get(key), int)
                }
            }
    return {
        "method": str(getattr(event, "method", type(event).__name__)),
        "payload": compact,
    }


def event_tool_name(event: dict[str, Any]) -> tuple[str, bool]:
    """Return a compact active-tool label and whether the event completes it."""

    method = str(event.get("method", ""))
    payload = event.get("payload", {})
    text = (
        json.dumps(payload, ensure_ascii=False).lower() if payload is not None else ""
    )
    is_tool = any(
        token in text
        for token in ("commandexecution", "mcptool", "tool_call", "toolcall")
    )
    completed = method.endswith("/completed") or method.endswith(".completed")
    if not is_tool:
        return "", completed
    if isinstance(payload, dict):
        item = payload.get("item", payload)
        if isinstance(item, dict):
            for key in ("tool_name", "toolName", "name", "type"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    label = (
                        "shell"
                        if value.lower() == "commandexecution"
                        else value.strip()
                    )
                    return label[:200], completed
    return "tool", completed


def event_token_usage(event: dict[str, Any]) -> int | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    usage = payload.get("token_usage", payload.get("tokenUsage"))
    if not isinstance(usage, dict):
        return None
    total = usage.get("total")
    if not isinstance(total, dict):
        return None
    value = total.get("total_tokens", total.get("totalTokens"))
    return value if isinstance(value, int) and value >= 0 else None


class ProgressWriter:
    def __init__(self, request: dict[str, Any], *, thread_id: str = "") -> None:
        self.request = request
        self.run_dir = Path(str(request["artifacts_dir"])).resolve().parent
        self.path = self.run_dir / "progress.json"
        self.events_path = self.run_dir / "raw-events" / "sdk-events.jsonl"
        self.thread_id = thread_id
        self.active_tool = ""
        self.last_sdk_event = ""
        self.tokens_used = 0
        self.sdk_thread_tokens = 0

    def update(
        self,
        *,
        phase: str,
        event: dict[str, Any] | None = None,
        stop_reason: str = "",
        tokens_used: int | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        if event is not None:
            self.last_sdk_event = str(event.get("method", ""))
            tool, completed = event_tool_name(event)
            if tool and not completed:
                self.active_tool = tool
            elif completed and tool:
                self.active_tool = ""
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"time": now, **event}, ensure_ascii=False) + "\n"
                )
            live_tokens = event_token_usage(event)
            if live_tokens is not None:
                # SDK notifications report the cumulative reusable-thread total,
                # including cached input and earlier roles. It is useful telemetry
                # but is not comparable to this role's billable token budget.
                self.sdk_thread_tokens = live_tokens
        if tokens_used is not None:
            self.tokens_used = max(0, tokens_used)
        if stop_reason:
            self.active_tool = ""
        payload = {
            "run_id": str(self.request.get("run_id", "")),
            "role": str(self.request.get("role", "")),
            "phase": phase,
            "last_sdk_event": self.last_sdk_event,
            "active_tool": self.active_tool,
            "last_progress_at": now,
            "tokens_used": self.tokens_used,
            "sdk_thread_tokens": self.sdk_thread_tokens,
            "token_budget": int(self.request.get("token_budget", 0) or 0),
            "stop_reason": stop_reason,
            "thread_id": self.thread_id,
            "execution_profile": str(
                self.request.get("execution_profile", "balanced")
            ),
            "model": str(self.request.get("model", "")),
            "reasoning_effort": str(
                self.request.get("reasoning_effort", "")
            ),
        }
        atomic_write_json(self.path, payload)
        return payload


def sdk_settings(request: dict[str, Any]) -> dict[str, str]:
    return validate_request_profile(request)


def sdk_sandbox(filesystem_access: str) -> Any:
    from openai_codex import Sandbox

    if filesystem_access in {
        "task_worktree_write",
        "workspace_write",
        "evidence_write",
    }:
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


def run_turn_streaming(
    thread: Any,
    prompt: str,
    *,
    settings: dict[str, str],
    schema: dict[str, Any],
    sandbox: Any,
    progress: ProgressWriter,
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
    turn_started: Callable[[Any], None] | None = None,
    manifest: dict[str, Any] | None = None,
    phase: str = "role",
) -> Any:
    """Run a turn while making every SDK notification observable."""

    snapshot = record_payload(
        request=progress.request, manifest=manifest or {}, prompt=prompt,
        output_schema=schema, runtime="codex-sdk", settings=settings,
        sandbox=str(getattr(sandbox, "value", sandbox)), thread_id=str(getattr(thread, "id", "")),
        phase=phase, control_root=ROOT,
    )
    prompt = snapshot["payload"]["prompt"]
    schema = snapshot["payload"]["output_schema"]
    settings = snapshot["payload"]["settings"]
    if not hasattr(thread, "turn"):
        return thread.run(
            prompt,
            effort=settings["reasoning_effort"],
            output_schema=schema,
            sandbox=sandbox,
            service_tier=settings["service_tier"],
        )
    from openai_codex.api import _collect_turn_result

    handle = thread.turn(
        prompt,
        effort=settings["reasoning_effort"],
        output_schema=schema,
        sandbox=sandbox,
        service_tier=settings["service_tier"],
    )
    if turn_started is not None:
        turn_started(handle)
    stream = handle.stream()
    events: list[Any] = []
    try:
        for notification in stream:
            events.append(notification)
            event = sdk_event_payload(notification)
            snapshot = progress.update(phase="sdk_turn", event=event)
            if progress_sink is not None:
                try:
                    progress_sink(snapshot)
                except (BrokenPipeError, ConnectionError, OSError) as exc:
                    with suppress(Exception):
                        handle.interrupt()
                    raise SessionDisconnected(
                        "role client disconnected during SDK turn"
                    ) from exc
    finally:
        stream.close()
    return _collect_turn_result(iter(events), turn_id=handle.id)


def run_sdk(
    *,
    request: dict[str, Any],
    prompt: str,
    output_contract: dict[str, Any],
    manifest: dict[str, Any],
    codex_client: Any | None = None,
    thread_id: str = "",
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
    turn_started: Callable[[Any], None] | None = None,
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
    try:
        settings = sdk_settings(request)
    except ModelPolicyError as exc:
        return failure_result(
            "Codex SDK execution profile is invalid.",
            [str(exc)],
            kind="policy_block",
            error_type="InvalidExecutionProfile",
        )
    request = {**request, **settings}
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
            str(
                (
                    Path(str(request["artifacts_dir"])).resolve().parent
                    / "tmp"
                    / str(request["role"])
                ).resolve()
            ),
        ]
        config_overrides.append(
            "sandbox_workspace_write.writable_roots=" + json.dumps(writable_roots)
        )

    before_snapshot = ""
    if not role_can_write_repository(filesystem_access):
        before_snapshot = git_snapshot(repository)
    started = time.monotonic()
    codex = codex_client
    owns_codex = codex is None
    progress = ProgressWriter(request, thread_id=thread_id)
    progress.update(phase="sdk_starting")
    try:
        if codex is None:
            codex = Codex(
                CodexConfig(
                    cwd=str(repository),
                    env=environment,
                    config_overrides=tuple(config_overrides),
                    client_name="ai_harness",
                    client_title="AI Harness",
                )
            )
        account = codex.account().account
        account_root = account.root if account is not None else None
        if str(getattr(account_root, "type", "")) != "chatgpt":
            return failure_result(
                "ChatGPT subscription authentication is required.",
                [
                    "Sign in to Codex with ChatGPT; API-key sessions are not accepted by this runtime."
                ],
                kind="policy_block",
                error_type="SubscriptionAuthRequired",
            )
        sdk_thread = None
        if thread_id:
            try:
                sdk_thread = codex.thread_resume(
                    thread_id,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(repository),
                    model=settings["model"],
                    sandbox=sdk_sandbox(filesystem_access),
                    service_tier=settings["service_tier"],
                )
            except Exception:
                progress.update(
                    phase="sdk_thread_recycle", stop_reason="thread_resume_failed"
                )
        if sdk_thread is None:
            sdk_thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=str(repository),
                ephemeral=False,
                model=settings["model"],
                sandbox=sdk_sandbox(filesystem_access),
                service_name="ai-harness",
                service_tier=settings["service_tier"],
            )
        thread_id = sdk_thread.id
        progress.thread_id = thread_id
        thread_snapshot = progress.update(phase="sdk_thread_ready")
        if progress_sink is not None:
            progress_sink(thread_snapshot)
        turn_result = run_turn_streaming(
            sdk_thread,
            prompt,
            settings=settings,
            schema=schema,
            sandbox=sdk_sandbox(filesystem_access),
            progress=progress,
            progress_sink=progress_sink,
            turn_started=turn_started,
            manifest=manifest,
        )
        response = turn_result.final_response or ""
        usage = usage_fields(turn_result)

        duration_ms = int((time.monotonic() - started) * 1000)
        write_raw_stream(
            request,
            manifest,
            json.dumps(
                {
                    "provider": "codex-sdk",
                    "thread_id": thread_id,
                    "turn_id": turn_result.id,
                    "status": getattr(
                        turn_result.status, "value", str(turn_result.status)
                    ),
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
        repair_thread = None
        for repair_attempt in range(1, MAX_OUTPUT_REPAIR_ATTEMPTS + 1):
            if not errors:
                break
            economy_settings = load_execution_profiles()["economy"]
            if repair_thread is None:
                repair_thread = codex.thread_resume(
                    thread_id,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(repository),
                    model=economy_settings["model"],
                    sandbox=sdk_sandbox("read_only"),
                    service_tier=economy_settings["service_tier"],
                )
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
            repaired_turn = run_turn_streaming(
                repair_thread,
                repair_prompt,
                settings=economy_settings,
                schema=schema,
                sandbox=sdk_sandbox("read_only"),
                progress=progress,
                progress_sink=progress_sink,
                turn_started=turn_started,
                manifest=manifest,
                phase=f"output_repair_{repair_attempt}",
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
    except ContextGuardError as exc:
        progress.update(phase="stopped", stop_reason="context_privacy_block")
        return failure_result("Context privacy check blocked execution.", [str(exc)], kind="policy_block", error_type="ContextGuardError")
    except Exception as exc:
        progress.update(phase="stopped", stop_reason=f"{type(exc).__name__}: {exc}")
        return failure_result(
            "Codex SDK execution failed.",
            [f"{type(exc).__name__}: {exc}"],
            kind="runtime_failure",
            error_type="SdkExecutionError",
        )
    finally:
        if owns_codex and codex is not None:
            with suppress(Exception):
                codex.close()

    if before_snapshot and git_snapshot(repository) != before_snapshot:
        return failure_result(
            "Read-only role changed repository contents.",
            ["git snapshot changed while sandbox was expected to be read-only"],
            kind="policy_block",
            error_type="ReadOnlyViolation",
        )
    result["thread_id"] = thread_id
    result["execution_profile"] = str(
        request.get("execution_profile", settings.get("execution_profile", "balanced"))
    )
    result["model"] = settings["model"]
    result["reasoning_effort"] = settings["reasoning_effort"]
    result["profile_reason"] = str(request.get("profile_reason", ""))
    result["escalation_level"] = int(request.get("escalation_level", 0) or 0)
    for name, value in usage.items():
        result[name] = value
    billable_tokens = (
        max(0, usage["input_tokens"] - usage["cached_input_tokens"])
        + usage["output_tokens"]
    )
    if billable_tokens:
        result["tokens_used"] = billable_tokens
    progress.update(
        phase="role_completed"
        if result.get("status") == "completed"
        else "role_stopped",
        stop_reason=str(result.get("status", "completed")),
        tokens_used=int(result.get("tokens_used", 0) or 0),
    )
    return result


def execute_via_session(
    socket_path: Path,
    *,
    request: dict[str, Any],
    prompt: str,
    output_contract: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    try:
        socket_stat = socket_path.stat()
    except OSError as exc:
        return failure_result(
            "Managed Codex SDK session socket is unavailable.",
            [str(exc)],
            kind="transient",
            error_type="SdkSessionUnavailable",
        )
    if (
        not stat.S_ISSOCK(socket_stat.st_mode)
        or socket_stat.st_uid != os.getuid()
        or stat.S_IMODE(socket_stat.st_mode) & 0o077
    ):
        return failure_result(
            "Managed Codex SDK session socket failed ownership validation.",
            ["session socket must be a private Unix socket owned by the current user"],
            kind="policy_block",
            error_type="UnsafeSdkSessionSocket",
        )
    payload = (
        json.dumps(
            {
                "action": "execute",
                "request": request,
                "prompt": prompt,
                "output_contract": output_contract,
                "manifest": manifest,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    if len(payload) > MAX_SESSION_MESSAGE_BYTES:
        return failure_result(
            "Codex SDK session request exceeded the configured limit.",
            [f"request bytes {len(payload)} exceed {MAX_SESSION_MESSAGE_BYTES}"],
            kind="runtime_failure",
            error_type="SessionRequestTooLarge",
        )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(
                float(request.get("timeout_seconds", 1800) or 1800) + 30.0
            )
            client.connect(str(socket_path))
            client.sendall(payload)
            buffer = b""
            while True:
                chunk = client.recv(64 * 1024)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_SESSION_MESSAGE_BYTES:
                    raise ValueError(
                        "Codex SDK session response exceeded the configured limit"
                    )
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line:
                        continue
                    message = json.loads(line.decode("utf-8"))
                    if not isinstance(message, dict):
                        continue
                    if message.get("type") == "result" and isinstance(
                        message.get("result"), dict
                    ):
                        return message["result"]
                    if message.get("type") == "error":
                        raise RuntimeError(
                            str(message.get("error", "SDK session failed"))
                        )
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        return failure_result(
            "Managed Codex SDK session failed.",
            [f"{type(exc).__name__}: {exc}"],
            kind="runtime_failure",
            error_type="SdkSessionError",
        )
    return failure_result(
        "Managed Codex SDK session closed without a result.",
        ["worker SDK session disconnected"],
        kind="transient",
        error_type="SdkSessionDisconnected",
    )


def execute_role() -> dict[str, Any]:
    request, request_errors = read_request()
    if request is None:
        return blocked_result("Role request could not be read.", request_errors)
    if request_errors:
        return blocked_result("Role request failed schema validation.", request_errors)
    prompt_text, prompt_errors = read_text_file(
        str(request["prompt_path"]), "prompt_path"
    )
    manifest, manifest_errors = read_context_manifest(str(request["context_manifest"]))
    output_contract, contract_errors = read_output_contract(
        str(request["output_contract"])
    )
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
    socket_value = os.environ.get(SESSION_SOCKET_ENV, "").strip()
    if socket_value:
        return execute_via_session(
            Path(socket_value),
            request=request,
            prompt=prompt,
            output_contract=output_contract,
            manifest=manifest,
        )
    return run_sdk(
        request=request,
        prompt=prompt,
        output_contract=output_contract,
        manifest=manifest,
    )


def main() -> int:
    try:
        result = execute_role()
    except ContextGuardError as exc:
        result = failure_result("Context validation blocked execution.", [str(exc)], kind="policy_block", error_type="ContextGuardError")
    result.setdefault("warnings", [])
    result.setdefault("blockers", [])
    result.setdefault("artifacts_created", [])
    result.setdefault("tokens_used", 0)
    result.setdefault("duration_ms", 0)
    result.setdefault("summary", "Role execution finished.")
    result.setdefault(
        "next_action", "blocked" if result.get("status") == "blocked" else "continue"
    )
    result.setdefault("status", "blocked")
    result.setdefault("finished_at", datetime.now(timezone.utc).isoformat())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
