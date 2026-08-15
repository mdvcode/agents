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
from codex_sdk_executor import sdk_settings, usage_fields


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
