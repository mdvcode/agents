"""Exercise the installed SDK event contract without authentication or a model turn."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from openai_codex import Sandbox
from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
)
from openai_codex.models import Notification


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/adapters"))
sys.path.insert(0, str(ROOT / "scripts"))

from codex_sdk_executor import ProgressWriter, run_turn_streaming, sdk_settings, usage_fields


def test_installed_sdk_stream_preserves_final_message_usage_and_progress(tmp_path: Path) -> None:
    counts = {"inputTokens": 100, "cachedInputTokens": 40, "outputTokens": 12, "reasoningOutputTokens": 3, "totalTokens": 112}
    notifications = [
        Notification("item/completed", ItemCompletedNotification.model_validate({
            "threadId": "thread-sdk", "turnId": "turn-sdk", "completedAtMs": 1,
            "item": {"type": "agentMessage", "id": "message-sdk", "phase": "final_answer", "text": "Stream completed"},
        })),
        Notification("thread/tokenUsage/updated", ThreadTokenUsageUpdatedNotification.model_validate({
            "threadId": "thread-sdk", "turnId": "turn-sdk", "tokenUsage": {"last": counts, "total": counts},
        })),
        Notification("turn/completed", TurnCompletedNotification.model_validate({
            "threadId": "thread-sdk", "turn": {"id": "turn-sdk", "items": [], "itemsView": "full", "status": "completed"},
        })),
    ]
    stream_closed: list[bool] = []

    def stream():
        try:
            yield from notifications
        finally:
            stream_closed.append(True)

    settings = sdk_settings({})

    def turn(prompt: str, **kwargs: object) -> SimpleNamespace:
        assert prompt == "Return a short message"
        assert kwargs["sandbox"] is Sandbox.read_only
        assert kwargs["effort"] == settings["reasoning_effort"]
        return SimpleNamespace(id="turn-sdk", stream=stream)

    request = {"run_id": "sdk-contract", "role": "planner", "repository": str(tmp_path), "artifacts_dir": str(tmp_path / "run/artifacts")}
    progress = ProgressWriter(request, thread_id="thread-sdk")
    result = run_turn_streaming(
        SimpleNamespace(id="thread-sdk", turn=turn), "Return a short message",
        settings=settings, schema={"type": "object"}, sandbox=Sandbox.read_only, progress=progress,
    )

    assert result.id == "turn-sdk"
    assert result.final_response == "Stream completed"
    assert usage_fields(result) == {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 12, "reasoning_output_tokens": 3}
    assert stream_closed == [True]
    events = [json.loads(line) for line in (tmp_path / "run/raw-events/sdk-events.jsonl").read_text().splitlines()]
    assert [event["method"] for event in events] == [entry.method for entry in notifications]
