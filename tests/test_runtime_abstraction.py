from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from runtimes.base import RuntimeDescriptor
from runtimes.codex_cli import CodexCliRuntime
from runtimes.codex_sdk import CodexSdkRuntime
from runtimes.registry import RuntimeConfigurationError, create_runtime, load_runtime_config
from runtimes.subprocess_runtime import SubprocessRuntime


def test_configured_runtime_is_local_codex_sdk_without_api_or_router() -> None:
    config = load_runtime_config()

    assert config["provider"] == "codex-sdk"
    assert config["transport"] == "local_subscription"
    assert config["api_required"] is False
    assert config["model_router"] is False


def test_registry_builds_the_primary_subscription_sdk_runtime() -> None:
    runtime = create_runtime()

    assert isinstance(runtime, CodexSdkRuntime)
    assert runtime.descriptor.provider == "codex-sdk"
    assert runtime.descriptor.kind == "runtime_adapter"
    assert runtime.descriptor.transport == "local_subscription"
    assert runtime.descriptor.production is True
    assert runtime.descriptor.api_required is False


def test_registry_rejects_future_provider_until_it_is_deliberately_added() -> None:
    with pytest.raises(RuntimeConfigurationError, match="unsupported runtime provider"):
        create_runtime(provider="openai-api")


def test_production_runtime_command_cannot_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_HARNESS_TEST_MODE", raising=False)

    with pytest.raises(RuntimeConfigurationError, match="restricted to harness test mode"):
        create_runtime(provider="codex-sdk", command="python fake-runtime.py")


def test_registry_keeps_codex_cli_as_explicit_compatibility_fallback() -> None:
    runtime = create_runtime(provider="codex-cli")

    assert isinstance(runtime, CodexCliRuntime)
    assert runtime.descriptor.command.endswith("scripts/adapters/codex_cli_executor.py")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("transport", "openai_api", "local_subscription"),
        ("api_required", True, "must not require a provider API"),
        ("model_router", True, "must disable model_router"),
    ),
)
def test_registry_enforces_step2_transport_and_router_boundary(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    config = {
        "version": 1,
        "runtime": {
            "provider": "codex-sdk",
            "executor_command": "python3 scripts/adapters/codex_sdk_executor.py",
            "transport": "local_subscription",
            "api_required": False,
            "model_router": False,
            "default_execution_profile": "balanced",
            "execution_profiles": {
                "complex": {"model": "gpt-5.6-sol", "reasoning_effort": "high", "service_tier": "fast"},
                "balanced": {"model": "gpt-5.6-terra", "reasoning_effort": "medium", "service_tier": "fast"},
                "economy": {"model": "gpt-5.6-luna", "reasoning_effort": "low", "service_tier": "fast"},
            },
            "require_account_type": "chatgpt",
        },
    }
    config["runtime"][field] = value
    path = tmp_path / ".agent-runtime.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError, match=message):
        load_runtime_config(path)


def test_harness_has_no_provider_specific_execution_dependency() -> None:
    harness = (SCRIPTS / "agent_role_runner.py").read_text(encoding="utf-8")

    forbidden = (
        "CodexAdapter",
        "check_codex_runtime",
        "codex_cli_executor.py",
        "openai.responses",
        "anthropic.messages",
    )
    assert all(value not in harness for value in forbidden)
    assert "runtime.execute(" in harness
    assert "runtime.preflight(" in harness


def test_subprocess_runtime_enforces_invocation_boundary_and_saves_provider_trace(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import json
import sys

request = json.load(sys.stdin)
print(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": request["role"] + " completed",
    "artifacts_created": [],
    "blockers": [],
    "warnings": [],
    "tokens_used": 2
}))
""".lstrip(),
        encoding="utf-8",
    )
    context = tmp_path / "context.json"
    context.write_text("{}\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    raw = tmp_path / "raw"
    descriptor = RuntimeDescriptor(
        provider="test-provider",
        kind="runtime_adapter",
        transport="test_fixture",
        production=False,
        command=f"{sys.executable} {adapter}",
        api_required=False,
    )
    runtime = SubprocessRuntime(descriptor=descriptor, raw_output_dir=raw)
    request = {
        "run_id": "run-runtime",
        "role": "planner",
        "goal": "Verify runtime abstraction",
        "repository": str(tmp_path.resolve()),
        "artifacts_dir": str(artifacts.resolve()),
        "context_manifest": str(context.resolve()),
        "prompt_path": str((ROOT / ".agents" / "prompts" / "planner.md").resolve()),
        "output_contract": str((ROOT / "schemas" / "role_result.schema.json").resolve()),
        "expected_artifacts": [],
        "allowed_tools": [],
        "allowed_artifacts": [],
        "filesystem_access": "read_only",
        "network_access": "none",
        "project_profile": "agent_workspace",
        "token_budget": 100,
        "timeout_seconds": 30,
    }

    result = runtime.execute(
        role="planner",
        context=context,
        task=request,
        worktree=tmp_path,
        artifacts=artifacts,
    )

    assert result["status"] == "completed"
    trace = json.loads((raw / "planner.json").read_text(encoding="utf-8"))
    assert trace["provider"] == "test-provider"

    result = runtime.execute(
        role="reviewer",
        context=context,
        task=request,
        worktree=tmp_path,
        artifacts=artifacts,
    )
    assert result["status"] == "blocked"
    assert "does not match invocation boundary" in result["blockers"][0]


def test_subprocess_runtime_uses_active_interpreter_for_python3_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    fake_python.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import json
import sys

request = json.load(sys.stdin)
print(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": request["role"] + " completed",
    "artifacts_created": [],
    "blockers": [],
    "warnings": [],
    "tokens_used": 1
}))
""".lstrip(),
        encoding="utf-8",
    )
    context = tmp_path / "context.json"
    context.write_text("{}\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    runtime = SubprocessRuntime(
        descriptor=RuntimeDescriptor(
            provider="test-provider",
            kind="runtime_adapter",
            transport="test_fixture",
            production=False,
            command=f"python3 {adapter}",
            api_required=False,
        )
    )
    request = {
        "run_id": "run-runtime-python",
        "role": "planner",
        "goal": "Use the active Python interpreter",
        "repository": str(tmp_path.resolve()),
        "artifacts_dir": str(artifacts.resolve()),
        "context_manifest": str(context.resolve()),
        "prompt_path": str((ROOT / ".agents" / "prompts" / "planner.md").resolve()),
        "output_contract": str((ROOT / "schemas" / "role_result.schema.json").resolve()),
        "expected_artifacts": [],
        "allowed_tools": [],
        "allowed_artifacts": [],
        "filesystem_access": "read_only",
        "network_access": "none",
        "project_profile": "agent_workspace",
        "token_budget": 100,
        "timeout_seconds": 30,
    }

    result = runtime.execute(
        role="planner",
        context=context,
        task=request,
        worktree=tmp_path,
        artifacts=artifacts,
    )

    assert result["status"] == "completed"
    assert result["summary"] == "planner completed"
