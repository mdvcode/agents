from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(SCRIPTS / "adapters") not in sys.path:
    sys.path.insert(0, str(SCRIPTS / "adapters"))

from check_codex_sdk_runtime import check_sdk
import codex_sdk_server
from codex_sdk_executor import ProgressWriter, execute_via_session, sdk_settings, usage_fields


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


def test_sdk_runtime_settings_default_to_sol_high_fast(monkeypatch: object) -> None:
    monkeypatch.delenv("AGENT_CODEX_MODEL", raising=False)
    monkeypatch.delenv("AGENT_CODEX_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("AGENT_CODEX_SERVICE_TIER", raising=False)

    assert sdk_settings() == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "service_tier": "fast",
    }


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

    assert live["tokens_used"] == 123
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

    class Connection:
        def __init__(self) -> None:
            self.messages: list[bytes] = []

        def sendall(self, payload: bytes) -> None:
            self.messages.append(payload)

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
    server.execute(Connection(), message)
    server.execute(Connection(), message)

    assert observed_thread_ids == ["", "thread-run-1"]
    assert server.state()["threads"] == {"run-1": "thread-run-1"}


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
