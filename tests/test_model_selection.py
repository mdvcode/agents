from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "scripts/adapters"):
    sys.path.insert(0, str(path))

from ai_harness.model_selection import ModelSelectionError, apply_model_override, normalize_model_override, require_available_model, resumed_model_override
from ai_harness.model_policy import ModelPolicyError, select_execution_profile
from codex_sdk_models import models_for_client
import codex_sdk_executor
import codex_sdk_server
from event_ingestion import EventError, enqueue_envelope, normalize_event
from run_state import task_fingerprint
from task_queue import TaskQueue
from worker_pool import safe_payload
from ai_harness.processes import ManagedProcessResult
import runtimes.codex_sdk
from runtimes.registry import discover_models
from check_codex_sdk_runtime import check_sdk


def model_entry(name: str = "available-custom-model", *, hidden: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        model=name, display_name="Available custom model", description="Provider entry",
        hidden=hidden, is_default=False, default_reasoning_effort="high", input_modalities=["text", "image"],
        supported_reasoning_efforts=[SimpleNamespace(reasoning_effort="high")],
    )


def catalog() -> dict[str, object]:
    return models_for_client(SimpleNamespace(models=lambda **_kw: SimpleNamespace(data=[model_entry()], next_cursor=None)))


def test_discovery_uses_visible_provider_model_identifiers_and_efforts() -> None:
    def models(*, include_hidden: bool) -> SimpleNamespace:
        assert include_hidden is False
        return SimpleNamespace(data=[model_entry(), model_entry("hidden-model", hidden=True)], next_cursor=None)

    value = models_for_client(SimpleNamespace(models=models))
    assert [item["id"] for item in value["models"]] == ["available-custom-model"]
    assert value["models"][0]["supported_reasoning_efforts"] == ["high"]
    assert require_available_model("available-custom-model", value)["default_reasoning_effort"] == "high"


def test_partial_catalog_is_unavailable_instead_of_claiming_complete_account_access() -> None:
    result = models_for_client(SimpleNamespace(models=lambda **_kw: SimpleNamespace(data=[model_entry()], next_cursor="more")))
    assert result["status"] == "unavailable"
    assert result["models"] == []


@pytest.mark.parametrize("account_type", ["chatgpt", "apiKey"])
def test_discovery_requires_subscription_and_matches_worker_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, account_type: str
) -> None:
    config: dict[str, object] = {}
    calls: list[str] = []

    class Client:
        def __init__(self, _config: object) -> None:
            pass

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def account(self) -> object:
            return SimpleNamespace(account=SimpleNamespace(root=SimpleNamespace(type=account_type)))

        def models(self, **_kwargs: object) -> object:
            calls.append("models")
            return SimpleNamespace(data=[model_entry()], next_cursor=None)

    sdk = ModuleType("openai_codex")
    sdk.Codex = Client
    sdk.CodexConfig = lambda **kwargs: config.update(kwargs)
    sdk.__version__ = "test"
    monkeypatch.setitem(sys.modules, "openai_codex", sdk)
    result = check_sdk(tmp_path, list_models=True)
    assert config["client_name"] == "ai_harness_worker"
    assert config["config_overrides"] == ("features.fast_mode=true",)
    if account_type == "chatgpt":
        assert result["status"] == "available"
        assert calls == ["models"]
    else:
        assert result["execution_status"] == "blocked"
        assert result["error_type"] == "SubscriptionAuthRequired"
        assert calls == []


@pytest.mark.parametrize("timed_out", [False, True])
def test_runtime_discovery_is_bounded_and_discards_private_transport_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timed_out: bool
) -> None:
    observations: list[dict[str, object]] = []

    def invoke(command: list[str], **kwargs: object) -> ManagedProcessResult:
        assert "--models" in command
        observations.append(kwargs)
        return ManagedProcessResult(returncode=0, stdout=json.dumps(catalog()), stderr="private transport details", duration_seconds=1, timed_out=timed_out)

    monkeypatch.setattr(runtimes.codex_sdk, "HARNESS_ROOT", tmp_path)
    monkeypatch.setattr(runtimes.codex_sdk, "run_managed_process", invoke)
    result = discover_models(worktree=tmp_path, provider="codex-sdk", timeout_seconds=120)
    assert observations[0]["timeout_seconds"] == 30
    assert not list((tmp_path / ".agent-queue/preflight").iterdir())
    assert "private transport details" not in json.dumps(result)
    assert result["status"] == ("unavailable" if timed_out else "available")
    assert result["models"] == ([] if timed_out else catalog()["models"])


@pytest.mark.parametrize("value", [None, 1, "model --unsafe", "model\nother", " model", "x" * 129])
def test_invalid_model_choice_is_rejected_at_intake(tmp_path: Path, value: object) -> None:
    with pytest.raises(ModelSelectionError):
        normalize_model_override(value)
    with pytest.raises(EventError, match="model_override"):
        normalize_event(source="cli", repository=tmp_path, payload={"external_id": "task-event", "task_id": "task", "goal": "Check file", "model_override": value})


@pytest.mark.parametrize("value", [{"status": "unavailable"}, {"status": "available", "models": []}])
def test_unavailable_model_is_not_replaced_silently(value: dict[str, object]) -> None:
    with pytest.raises(ModelSelectionError):
        require_available_model("available-custom-model", value)


@pytest.mark.parametrize("mode", ["auto", "fast", "full", "adaptive", "goal"])
def test_model_choice_survives_task_envelope_and_worker_payload_without_changing_mode(tmp_path: Path, mode: str) -> None:
    envelope = normalize_event(
        source="cli", repository=tmp_path,
        payload={"external_id": "task-event", "task_id": "task", "goal": "Check file", "mode": mode, "model_override": "available-custom-model"},
    )
    record = enqueue_envelope(TaskQueue(tmp_path / "queue.db"), envelope)
    payload = safe_payload(record)
    assert payload["model_override"] == "available-custom-model"
    assert payload["mode"] == mode
    assert payload["runtime_provider"] == "codex-sdk"


def test_resume_preserves_selection_and_rejects_switching_models() -> None:
    state = {"model_override": "available-custom-model"}
    assert resumed_model_override("", state) == "available-custom-model"
    assert resumed_model_override("available-custom-model", state) == "available-custom-model"
    assert resumed_model_override("", {}) == ""
    with pytest.raises(ModelSelectionError, match="changed"):
        resumed_model_override("other-model", state)


def test_selection_changes_task_identity_but_empty_choice_keeps_legacy_identity(tmp_path: Path) -> None:
    args = dict(task_id="task", goal="Check", repository=tmp_path, branch="feat/task", base_branch="main")
    legacy = task_fingerprint(**args)
    assert task_fingerprint(**args, model_override="") == legacy
    assert task_fingerprint(**args, model_override="available-custom-model") != legacy


def test_user_choice_keeps_bounded_profile_gates_and_provider_validation() -> None:
    settings = select_execution_profile(role="implementation-agent", goal="Fix", risk_class="low", changed_files=[], changed_lines=0, changed_areas=[], bounded_escalation_exhausted=True)
    chosen = apply_model_override(settings, "available-custom-model")
    assert chosen["terminal_action"] == settings["terminal_action"] == "human_or_dead_letter"
    assert chosen["execution_profile"] == settings["execution_profile"]
    assert chosen["service_tier"] == settings["service_tier"] == "fast"
    with pytest.raises(ModelPolicyError):
        codex_sdk_executor.sdk_settings({"model": "available-custom-model"})
    with pytest.raises(ModelPolicyError):
        codex_sdk_executor.sdk_settings({"model": "available-custom-model", "model_override": "available-custom-model"})
    validated = codex_sdk_executor.sdk_settings({"model": "available-custom-model", "model_override": "available-custom-model"}, model_catalog=catalog())
    assert validated == {"execution_profile": "balanced", "model": "available-custom-model", "reasoning_effort": "high", "service_tier": "fast"}


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("resume_thread", ["", "saved-thread"])
@pytest.mark.parametrize("repair_output", [False, True])
def test_executor_revalidates_choice_before_start_or_resume_and_uses_supported_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, available: bool, resume_thread: str, repair_output: bool
) -> None:
    sdk = ModuleType("openai_codex")
    sdk.Codex = object
    sdk.CodexConfig = object
    sdk.ApprovalMode = SimpleNamespace(deny_all="never")
    sdk.Sandbox = SimpleNamespace(workspace_write="workspace-write", read_only="read-only")
    monkeypatch.setitem(sys.modules, "openai_codex", sdk)
    observed: list[tuple[str, dict[str, object]]] = []

    class Client:
        def account(self) -> object:
            return SimpleNamespace(account=SimpleNamespace(root=SimpleNamespace(type="chatgpt")))

        def models(self, **kwargs: object) -> object:
            return SimpleNamespace(data=[model_entry()] if available else [], next_cursor=None)

        def thread_start(self, **kwargs: object) -> object:
            observed.append(("start", kwargs))
            return SimpleNamespace(id="new-thread")

        def thread_resume(self, thread_id: str, **kwargs: object) -> object:
            observed.append(("resume", {"thread_id": thread_id, **kwargs}))
            return SimpleNamespace(id=thread_id)

    def turn(thread: object, prompt: str, **kwargs: object) -> object:
        assert kwargs["settings"]["model"] == "available-custom-model"
        assert kwargs["settings"]["reasoning_effort"] == "high"
        return SimpleNamespace(id="turn", status="completed", usage=None, final_response=json.dumps({
            "status": "completed", "next_action": "continue", "summary": "Done", "artifacts_created": [], "blockers": [], "warnings": [], "tokens_used": 1,
        }))

    monkeypatch.setattr(codex_sdk_executor, "run_turn_streaming", turn)
    validation_results = iter([["invalid output"], []] if repair_output else [[]])
    monkeypatch.setattr(codex_sdk_executor, "validate_candidate", lambda _request, _result: next(validation_results))
    monkeypatch.setattr(codex_sdk_executor, "write_raw_stream", lambda *args, **kwargs: None)
    request = {
        "run_id": "run", "role": "implementation-agent", "repository": str(tmp_path), "artifacts_dir": str(tmp_path / "run/artifacts"),
        "filesystem_access": "task_worktree_write", "execution_profile": "balanced", "model": "available-custom-model", "model_override": "available-custom-model",
        "reasoning_effort": "medium", "service_tier": "fast",
    }
    result = codex_sdk_executor.run_sdk(request=request, prompt="Check", output_contract={}, manifest={}, codex_client=Client(), thread_id=resume_thread)
    if available:
        assert result["status"] == "completed", result
        assert result["model"] == "available-custom-model"
        assert result["reasoning_effort"] == "high"
        assert observed[0][0] == ("resume" if resume_thread else "start")
        assert observed[0][1]["model"] == "available-custom-model"
        assert observed[0][1]["approval_mode"] == "never"
        assert len(observed) == (2 if repair_output else 1)
        if repair_output:
            assert observed[1][0] == "resume"
            assert observed[1][1]["model"] == "available-custom-model"
            assert observed[1][1]["sandbox"] == "read-only"
    else:
        assert result["status"] == "blocked"
        assert result["_failure"]["error_type"] == "ModelUnavailable"
        assert observed == []


def test_session_model_choice_separates_reused_threads_and_verifiers_remain_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[str] = []

    def execute(**kwargs: object) -> dict[str, str]:
        observed.append(str(kwargs["thread_id"]))
        return {"status": "completed", "thread_id": f"thread-{len(observed)}"}

    monkeypatch.setattr(codex_sdk_server, "run_sdk", execute)
    server = codex_sdk_server.CodexSdkServer(socket_path=tmp_path / "sdk.sock", state_path=tmp_path / "sdk.json", max_requests=10, max_age_seconds=60)
    server.codex = object()
    connection = SimpleNamespace(sendall=lambda _data: None)
    for role, model in (("implementation-agent", "model-a"), ("implementation-agent", "model-a"), ("implementation-agent", "model-b"), ("reviewer", "model-a"), ("implementation-agent", "model-a")):
        server.execute(connection, {"request": {"run_id": "run", "role": role, "model_override": model}, "prompt": "Check", "output_contract": {}, "manifest": {}})
    assert observed == ["", "thread-1", "", "", "thread-2"]
