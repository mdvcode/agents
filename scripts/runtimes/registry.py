"""Load one configured runtime provider without model-routing decisions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ai_harness.model_policy import ModelPolicyError, load_execution_profiles
from runtimes.base import Runtime, RuntimeDescriptor
from runtimes.codex_cli import CodexCliRuntime
from runtimes.codex_sdk import CodexSdkRuntime
from runtimes.subprocess_runtime import SubprocessRuntime


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CONFIG = ROOT / ".agent-runtime.yaml"
SUPPORTED_PROVIDERS = {"codex-cli", "codex-sdk"}


class RuntimeConfigurationError(ValueError):
    """Raised when runtime configuration violates the local-subscription contract."""


def load_runtime_config(path: Path = RUNTIME_CONFIG) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeConfigurationError(f"cannot read runtime configuration: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RuntimeConfigurationError(".agent-runtime.yaml must contain version: 1")
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        raise RuntimeConfigurationError(".agent-runtime.yaml must contain runtime object")
    provider = runtime.get("provider")
    if provider not in SUPPORTED_PROVIDERS:
        raise RuntimeConfigurationError(f"unsupported runtime provider: {provider!r}")
    if runtime.get("transport") != "local_subscription":
        raise RuntimeConfigurationError("production Codex runtime must use local_subscription transport")
    if runtime.get("api_required") is not False:
        raise RuntimeConfigurationError("production Codex runtime must not require a provider API")
    if runtime.get("model_router") is not False:
        raise RuntimeConfigurationError("production runtime configuration must disable model_router")
    if provider == "codex-sdk":
        if runtime.get("require_account_type") != "chatgpt":
            raise RuntimeConfigurationError("codex-sdk runtime must require ChatGPT subscription authentication")
        if runtime.get("default_execution_profile") != "balanced":
            raise RuntimeConfigurationError("codex-sdk runtime must default to the balanced execution profile")
        try:
            profiles = load_execution_profiles(path)
        except ModelPolicyError as exc:
            raise RuntimeConfigurationError(str(exc)) from exc
        expected_profiles = {
            "complex": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "service_tier": "fast",
            },
            "balanced": {
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
                "service_tier": "fast",
            },
            "economy": {
                "model": "gpt-5.6-luna",
                "reasoning_effort": "low",
                "service_tier": "fast",
            },
        }
        if profiles != expected_profiles:
            raise RuntimeConfigurationError(
                "codex-sdk execution profiles must be Sol/high, Terra/medium, and Luna/low"
            )
    return runtime


def create_runtime(
    *,
    provider: str = "",
    command: str = "",
    timeout_seconds: int = 600,
    raw_output_dir: Path | None = None,
) -> Runtime:
    config = load_runtime_config()
    explicit_provider = provider or os.environ.get("AGENT_RUNTIME_PROVIDER", "")
    selected = explicit_provider or str(config["provider"])
    command_override = command or os.environ.get("AGENT_RUNTIME_COMMAND", "")
    test_mode = os.environ.get("AGENT_HARNESS_TEST_MODE") == "1"
    if command_override and (not explicit_provider or selected == "test-subprocess"):
        return SubprocessRuntime(
            descriptor=RuntimeDescriptor(
                provider="test-subprocess",
                kind="runtime_adapter",
                transport="test_fixture",
                production=False,
                command=command_override,
                api_required=False,
            ),
            timeout_seconds=timeout_seconds,
            raw_output_dir=raw_output_dir,
        )
    if command_override and selected in {"codex-cli", "codex-sdk"} and not test_mode:
        raise RuntimeConfigurationError(
            "production Codex command overrides are restricted to harness test mode"
        )
    if selected not in SUPPORTED_PROVIDERS:
        raise RuntimeConfigurationError(f"unsupported runtime provider: {selected!r}")
    configured_command = config.get("executor_command", "")
    if selected == "codex-cli" and config.get("provider") != "codex-cli":
        configured_command = config.get("fallback_executor_command", "")
    executor_command = command_override if test_mode and command_override else str(configured_command)
    if not executor_command:
        raise RuntimeConfigurationError(f"{selected} runtime executor_command is required")
    if selected == "codex-sdk":
        return CodexSdkRuntime(
            command=executor_command,
            timeout_seconds=timeout_seconds,
            raw_output_dir=raw_output_dir,
        )
    return CodexCliRuntime(
        command=executor_command,
        timeout_seconds=timeout_seconds,
        raw_output_dir=raw_output_dir,
    )
