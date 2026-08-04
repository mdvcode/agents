from __future__ import annotations

import json
from pathlib import Path

from ai_harness.observability import NoOpTelemetryRuntime, TelemetryRuntime
from ai_harness.observability import telemetry as telemetry_module
from ai_harness.observability.store import recent_spans, trace_summary


def test_local_span_export_is_sanitized_and_preserves_parentage(tmp_path: Path) -> None:
    run_dir = tmp_path / ".agent-runs" / "trace-run"
    parent_runtime = TelemetryRuntime(run_dir=run_dir, service_name="test-parent")
    child_runtime = TelemetryRuntime(run_dir=run_dir, service_name="test-child")
    carrier: dict[str, str] = {}
    with parent_runtime.span(
        "parent",
        {"task.id": 7, "prompt": "must never be exported", "authorization": "hidden"},
    ) as parent:
        parent_runtime.inject_environment(carrier)
        with child_runtime.span(
            "child",
            {"step.name": "quality", "stderr": "private output"},
            context=child_runtime.extracted_context(carrier),
        ) as child:
            child.add_event("checked", {"result": "pass", "secret.value": "hidden"})
    child_runtime.shutdown()
    parent_runtime.shutdown()

    spans = recent_spans(tmp_path / ".agent-runs")
    by_name = {item["name"]: item for item in spans}
    assert by_name["child"]["trace_id"] == by_name["parent"]["trace_id"]
    assert by_name["child"]["parent_span_id"] == by_name["parent"]["span_id"]
    serialized = json.dumps(spans)
    assert "must never be exported" not in serialized
    assert "private output" not in serialized
    assert "hidden" not in serialized
    assert by_name["parent"]["attributes"]["task.id"] == 7


def test_trace_store_is_bounded_and_summarized(tmp_path: Path) -> None:
    run_dir = tmp_path / ".agent-runs" / "bounded"
    runtime = TelemetryRuntime(run_dir=run_dir)
    for index in range(5):
        with runtime.span("operation", {"index": index}):
            pass
    runtime.shutdown()

    spans = recent_spans(tmp_path / ".agent-runs", limit=3)
    summary = trace_summary(tmp_path / ".agent-runs", limit=3)

    assert len(spans) == 3
    assert summary["count"] == 3
    assert summary["duration_ms"]["p95"] is not None


def test_telemetry_initialization_failure_is_fail_open(monkeypatch: object) -> None:
    def fail(**_kwargs: object) -> None:
        raise RuntimeError("collector unavailable")

    monkeypatch.setattr(telemetry_module, "TelemetryRuntime", fail)

    runtime = telemetry_module.safe_telemetry_runtime(service_name="test")

    assert isinstance(runtime, NoOpTelemetryRuntime)
    with runtime.span("still-runs"):
        runtime.task_counter.add(1)
