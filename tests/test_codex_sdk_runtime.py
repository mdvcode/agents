from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(SCRIPTS / "adapters") not in sys.path:
    sys.path.insert(0, str(SCRIPTS / "adapters"))

from check_codex_sdk_runtime import check_sdk
from ai_harness.sdk_session import MAX_UNIX_SOCKET_PATH_BYTES, ManagedCodexSdkSession
import codex_sdk_server
import codex_sdk_executor
from codex_sdk_executor import (
    ProgressWriter,
    execute_via_session,
    sdk_run_input,
    sdk_settings,
    usage_fields,
)


class FakeCodex:
    def __init__(self, _config: object) -> None:
        pass

    def __enter__(self) -> "FakeCodex":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def account(self) -> object:
        return SimpleNamespace(
            account=SimpleNamespace(
                root=SimpleNamespace(type="chatgpt", plan_type=SimpleNamespace(value="plus"))
            )
        )


class FakeConfig:
    def __init__(self, **_kwargs: object) -> None:
        pass


class FakeTextInput:
    def __init__(self, *, text: str) -> None:
        self.text = text


class FakeLocalImageInput:
    def __init__(self, *, path: str) -> None:
        self.path = path


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[bytes] = []

    def sendall(self, payload: bytes) -> None:
        self.messages.append(payload)


def test_sdk_preflight_requires_and_reports_chatgpt_subscription(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    module = ModuleType("openai_codex")
    module.Codex = FakeCodex  # type: ignore[attr-defined]
    module.CodexConfig = FakeConfig  # type: ignore[attr-defined]
    module.__version__ = "test"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai_codex", module)

    result = check_sdk(tmp_path)

    assert result["execution_status"] == "completed"
    assert result["account_type"] == "chatgpt"
    assert result["plan_type"] == "plus"


def test_sdk_runtime_settings_default_to_balanced_profile(monkeypatch: object) -> None:
    monkeypatch.delenv("AGENT_CODEX_MODEL", raising=False)
    monkeypatch.delenv("AGENT_CODEX_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("AGENT_CODEX_SERVICE_TIER", raising=False)

    assert sdk_settings({}) == {
        "execution_profile": "balanced",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "service_tier": "fast",
    }


def test_sdk_run_input_keeps_text_only_turn_compatible(monkeypatch: object) -> None:
    monkeypatch.setattr(codex_sdk_executor, "attachment_image_paths", lambda _manifest: [])

    assert sdk_run_input("task prompt", {}) == "task prompt"


def test_sdk_run_input_adds_only_revalidated_local_images(monkeypatch: object) -> None:
    module = ModuleType("openai_codex")
    module.TextInput = FakeTextInput  # type: ignore[attr-defined]
    module.LocalImageInput = FakeLocalImageInput  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai_codex", module)
    monkeypatch.setattr(
        codex_sdk_executor,
        "attachment_image_paths",
        lambda _manifest: ["/private/run/inputs/derived/page-1.png"],
    )

    value = sdk_run_input("task prompt", {"attachment_context": {}})

    assert len(value) == 2
    assert isinstance(value[0], FakeTextInput)
    assert value[0].text == "task prompt"
    assert isinstance(value[1], FakeLocalImageInput)
    assert value[1].path == "/private/run/inputs/derived/page-1.png"


def test_sdk_usage_uses_last_turn_and_preserves_cached_tokens() -> None:
    turn = SimpleNamespace(
        usage=SimpleNamespace(
            last=SimpleNamespace(
                input_tokens=100,
                cached_input_tokens=80,
                output_tokens=15,
                reasoning_output_tokens=7,
            )
        )
    )

    assert usage_fields(turn) == {
        "input_tokens": 100,
        "cached_input_tokens": 80,
        "output_tokens": 15,
        "reasoning_output_tokens": 7,
    }


def test_progress_writer_records_sdk_event_tool_budget_and_stop_reason(tmp_path: Path) -> None:
    artifacts = tmp_path / "run" / "artifacts"
    artifacts.mkdir(parents=True)
    writer = ProgressWriter(
        {
            "run_id": "run-1",
            "role": "implementation-agent",
            "artifacts_dir": str(artifacts),
            "token_budget": 12000,
        },
        thread_id="thread-1",
    )

    writer.update(
        phase="sdk_turn",
        event={
            "method": "item/started",
            "payload": {"item": {"type": "commandExecution", "command": "pytest -q"}},
        },
    )
    live = writer.update(
        phase="sdk_turn",
        event={
            "method": "thread/tokenUsage/updated",
            "payload": {"token_usage": {"total": {"total_tokens": 123}}},
        },
    )
    final = writer.update(phase="role_completed", stop_reason="completed", tokens_used=321)

    assert live["tokens_used"] == 0
    assert live["sdk_thread_tokens"] == 123
    assert final["phase"] == "role_completed"
    assert final["active_tool"] == ""
    assert final["tokens_used"] == 321
    assert final["token_budget"] == 12000
    assert final["stop_reason"] == "completed"
    assert final["thread_id"] == "thread-1"
    assert (tmp_path / "run" / "raw-events" / "sdk-events.jsonl").is_file()


def test_worker_sdk_server_reuses_run_bound_thread(monkeypatch: object, tmp_path: Path) -> None:
    observed_thread_ids: list[str] = []

    def fake_run_sdk(**kwargs: object) -> dict[str, object]:
        observed_thread_ids.append(str(kwargs["thread_id"]))
        return {"status": "completed", "thread_id": "thread-run-1"}

    monkeypatch.setattr(codex_sdk_server, "run_sdk", fake_run_sdk)
    server = codex_sdk_server.CodexSdkServer(
        socket_path=tmp_path / "sdk.sock",
        state_path=tmp_path / "sdk.json",
        max_requests=10,
        max_age_seconds=600,
    )
    server.codex = object()

    message = {
        "request": {
            "run_id": "run-1",
            "repository": str(tmp_path),
            "artifacts_dir": str(tmp_path / "run" / "artifacts"),
        },
        "prompt": "continue",
        "output_contract": {},
        "manifest": {},
    }
    roles = ("planner", "risk-classifier", "implementation-agent", "test-generator", "ci-repair-agent")
    for role in roles:
        message["request"]["role"] = role
        server.execute(FakeConnection(), message)

    assert observed_thread_ids == ["", *(["thread-run-1"] * (len(roles) - 1))]
    assert server.state()["threads"] == {"run-1": "thread-run-1"}


@pytest.mark.parametrize(
    "verifier_role",
    ["reviewer", "security-agent", "frontend-qa-agent", "architecture-consistency-agent", "semantic-conflict-agent"],
)
def test_worker_sdk_verifiers_start_fresh_without_replacing_writer_thread(
    monkeypatch: object, tmp_path: Path, verifier_role: str
) -> None:
    observed: list[tuple[str, str]] = []
    emitted_thread_ids: list[str] = []
    server_options = {
        "socket_path": tmp_path / "sdk.sock",
        "state_path": tmp_path / "sdk.json",
        "max_requests": 10,
        "max_age_seconds": 600,
    }
    server = codex_sdk_server.CodexSdkServer(**server_options)
    server.codex = object()
    manifest = {"filesystem_access": "read_only"}
    contract = {"type": "object"}

    def fake_run_sdk(**kwargs: object) -> dict[str, object]:
        request = kwargs["request"]
        role = request["role"]
        observed.append((role, kwargs["thread_id"]))
        thread_id = kwargs["thread_id"] or f"thread-{len(observed)}"
        emitted_thread_ids.append(thread_id)
        assert kwargs["manifest"] is manifest
        assert kwargs["output_contract"] is contract
        assert kwargs["prompt"] == "current task and evidence"
        assert request["filesystem_access"] == ("read_only" if role == verifier_role else "workspace_write")
        assert request["model"] == "gpt-5.6-terra"
        assert request["service_tier"] == "fast"
        assert kwargs["codex_client"] is server.codex
        kwargs["progress_sink"]({"thread_id": thread_id})
        if role == verifier_role:
            assert server.state()["threads"] == {"run-1": "thread-1"}
        return {"status": "completed", "thread_id": thread_id}

    monkeypatch.setattr(codex_sdk_server, "run_sdk", fake_run_sdk)
    roles = ("implementation-agent", verifier_role, "ci-repair-agent", verifier_role, "test-generator")
    for index, role in enumerate(roles):
        if index == 2:
            # A worker recycle must restore only the writer's conversation.
            server = codex_sdk_server.CodexSdkServer(**server_options)
            server.codex = object()
        server.execute(
            FakeConnection(),
            {
                "request": {
                    "run_id": "run-1",
                    "role": role,
                    "repository": str(tmp_path),
                    "filesystem_access": "read_only" if role == verifier_role else "workspace_write",
                    "model": "gpt-5.6-terra",
                    "service_tier": "fast",
                },
                "prompt": "current task and evidence",
                "output_contract": contract,
                "manifest": manifest,
            },
        )
        assert server.state()["threads"] == {"run-1": "thread-1"}

    assert observed == [
        ("implementation-agent", ""),
        (verifier_role, ""),
        ("ci-repair-agent", "thread-1"),
        (verifier_role, ""),
        ("test-generator", "thread-1"),
    ]
    assert emitted_thread_ids == ["thread-1", "thread-2", "thread-1", "thread-4", "thread-1"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_verifier_does_not_seed_a_later_writer_thread(
    monkeypatch: object, tmp_path: Path, interrupted: bool
) -> None:
    observed_thread_ids: list[str] = []
    server = codex_sdk_server.CodexSdkServer(
        socket_path=tmp_path / "sdk.sock",
        state_path=tmp_path / "sdk.json",
        max_requests=10,
        max_age_seconds=600,
    )
    server.codex = object()

    def fake_run_sdk(**kwargs: object) -> dict[str, object]:
        observed_thread_ids.append(kwargs["thread_id"])
        if kwargs["request"]["role"] == "reviewer":
            kwargs["progress_sink"]({"thread_id": "failed-review-thread"})
            assert server.state()["threads"] == {}
            if interrupted:
                raise ConnectionError("review interrupted")
            return {"status": "failed", "thread_id": "failed-review-thread"}
        return {"status": "completed", "thread_id": "writer-thread"}

    monkeypatch.setattr(codex_sdk_server, "run_sdk", fake_run_sdk)
    message = {
        "request": {"run_id": "run-1", "role": "reviewer", "repository": str(tmp_path)},
        "prompt": "current task and evidence",
        "output_contract": {},
        "manifest": {},
    }
    if interrupted:
        with pytest.raises(ConnectionError, match="review interrupted"):
            server.execute(FakeConnection(), message)
    else:
        server.execute(FakeConnection(), message)
    message["request"]["role"] = "implementation-agent"
    server.execute(FakeConnection(), message)

    assert observed_thread_ids == ["", ""]
    assert server.state()["threads"] == {"run-1": "writer-thread"}


def test_sdk_session_rejects_non_socket_transport(tmp_path: Path) -> None:
    unsafe = tmp_path / "not-a-socket"
    unsafe.write_text("capture prompts", encoding="utf-8")

    result = execute_via_session(
        unsafe,
        request={"timeout_seconds": 1},
        prompt="private prompt",
        output_contract={},
        manifest={},
    )

    assert result["status"] == "blocked"
    assert result["_failure"]["error_type"] == "UnsafeSdkSessionSocket"


def test_managed_sdk_session_uses_short_private_socket_for_long_install_path(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / ("installed-harness-" * 5) / ".agent-queue" / "sdk-sessions"
    session = ManagedCodexSdkSession(
        worker_id="worker-service-long-install-1",
        harness_root=ROOT,
        state_root=state_root,
        startup_timeout_seconds=5.0,
    )

    assert len(bytes(str(state_root / "sdk-placeholder.sock"), "utf-8")) > MAX_UNIX_SOCKET_PATH_BYTES
    assert len(bytes(str(session.socket_path), "utf-8")) <= MAX_UNIX_SOCKET_PATH_BYTES
    assert session.socket_path.parent != state_root

    try:
        session.ensure()
        assert session.heartbeat() is True
        assert session.state_path.parent == state_root.resolve()
        assert session.state()["socket_path"] == str(session.socket_path)
        assert session.socket_path.parent.stat().st_mode & 0o077 == 0
    finally:
        session.close()
