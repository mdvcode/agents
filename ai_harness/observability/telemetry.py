"""Fail-open OpenTelemetry setup with a sanitized local span exporter."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Status, StatusCode


DENIED_ATTRIBUTE_PARTS = {
    "authorization",
    "credential",
    "goal",
    "message",
    "output",
    "password",
    "prompt",
    "secret",
    "stderr",
    "stdout",
    "token",
}
MAX_ATTRIBUTE_STRING = 256
MAX_ATTRIBUTE_SEQUENCE = 16


def _safe_key(key: object) -> str:
    return str(key)[:128]


def _safe_value(value: object) -> str | bool | int | float | list[str | bool | int | float]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:MAX_ATTRIBUTE_SEQUENCE]]  # type: ignore[list-item]
    return str(value)[:MAX_ATTRIBUTE_STRING]


def sanitize_attributes(attributes: Mapping[object, object] | None) -> dict[str, Any]:
    """Return bounded operational metadata, excluding content-bearing fields."""

    safe: dict[str, Any] = {}
    for raw_key, value in (attributes or {}).items():
        key = _safe_key(raw_key)
        lowered = key.lower()
        if any(part in lowered for part in DENIED_ATTRIBUTE_PARTS):
            continue
        safe[key] = _safe_value(value)
    return safe


class JsonlSpanExporter(SpanExporter):
    """Append compact, sanitized finished spans to run-scoped JSONL evidence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def export(self, spans: tuple[ReadableSpan, ...]) -> SpanExportResult:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            records = [json.dumps(self._record(span), sort_keys=True) for span in spans]
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                for record in records:
                    handle.write(record + "\n")
        except OSError:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    @staticmethod
    def _record(span: ReadableSpan) -> dict[str, Any]:
        context = span.context
        parent = span.parent
        status = span.status.status_code.name.lower()
        return {
            "schema_version": 1,
            "name": span.name[:128],
            "trace_id": f"{context.trace_id:032x}",
            "span_id": f"{context.span_id:016x}",
            "parent_span_id": f"{parent.span_id:016x}" if parent is not None else "",
            "start_time_unix_nano": int(span.start_time or 0),
            "end_time_unix_nano": int(span.end_time or 0),
            "duration_ms": round(max(0, int(span.end_time or 0) - int(span.start_time or 0)) / 1_000_000, 3),
            "status": status,
            "attributes": sanitize_attributes(span.attributes),
            "events": [
                {
                    "name": event.name[:128],
                    "time_unix_nano": int(event.timestamp or 0),
                    "attributes": sanitize_attributes(event.attributes),
                }
                for event in span.events[:32]
            ],
        }

    def shutdown(self) -> None:
        return


class TelemetryRuntime:
    """Own providers for one Harness process without mutating global providers."""

    def __init__(
        self,
        *,
        run_dir: Path | None = None,
        service_name: str = "ai-harness",
        service_instance_id: str = "",
    ) -> None:
        resource_attributes = {"service.name": os.environ.get("OTEL_SERVICE_NAME", service_name)}
        if service_instance_id:
            resource_attributes["service.instance.id"] = service_instance_id
        resource = Resource.create(resource_attributes)
        self.trace_provider = TracerProvider(resource=resource)
        if run_dir is not None:
            self.trace_provider.add_span_processor(
                SimpleSpanProcessor(JsonlSpanExporter(run_dir / "raw-events" / "otel-spans.jsonl"))
            )
        self._configure_otlp_trace_exporter()
        self.tracer = self.trace_provider.get_tracer("ai_harness.observability", "1")
        readers = self._metric_readers()
        self.meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        self.meter: Meter = self.meter_provider.get_meter("ai_harness.observability", "1")
        self.task_counter = self.meter.create_counter("ai_harness.tasks", unit="{task}")
        self.retry_counter = self.meter.create_counter("ai_harness.retries", unit="{retry}")
        self.loop_counter = self.meter.create_counter("ai_harness.loops", unit="{iteration}")
        self.failure_counter = self.meter.create_counter("ai_harness.failures", unit="{failure}")
        self.recovery_attempts_total = self.meter.create_counter("recovery_attempts_total", unit="{attempt}")
        self.recovery_success_total = self.meter.create_counter("recovery_success_total", unit="{recovery}")
        self.recovery_exhausted_total = self.meter.create_counter("recovery_exhausted_total", unit="{task}")
        self.task_retries_total = self.meter.create_counter("task_retries_total", unit="{retry}")
        self.output_repairs_total = self.meter.create_counter("output_repairs_total", unit="{repair}")
        self.resume_success_total = self.meter.create_counter("resume_success_total", unit="{resume}")
        self.worker_crashes_total = self.meter.create_counter("worker_crashes_total", unit="{crash}")
        self.duration_histogram = self.meter.create_histogram(
            "ai_harness.duration", unit="s", description="Harness operation latency"
        )
        self._closed = False

    def _configure_otlp_trace_exporter(self) -> None:
        if not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")):
            return
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            self.trace_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except (ImportError, OSError, ValueError):
            return

    @staticmethod
    def _metric_readers() -> list[PeriodicExportingMetricReader]:
        if not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")):
            return []
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

            return [PeriodicExportingMetricReader(OTLPMetricExporter())]
        except (ImportError, OSError, ValueError):
            return []

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, object] | None = None,
        *,
        context: Context | None = None,
    ) -> Iterator[Span]:
        """Create a span that records error type but never exception content."""

        with self.tracer.start_as_current_span(
            name,
            context=context,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            for key, value in sanitize_attributes(attributes).items():
                span.set_attribute(key, value)
            try:
                yield span
                if span.status.status_code is StatusCode.UNSET:
                    span.set_status(Status(StatusCode.OK))
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                self.failure_counter.add(1, {"operation": name[:64], "error.type": type(exc).__name__})
                raise

    def extracted_context(self, carrier: Mapping[str, str] | None = None) -> Context:
        return propagate.extract(carrier or os.environ)

    @staticmethod
    def inject_environment(environment: dict[str, str]) -> dict[str, str]:
        propagate.inject(environment)
        return environment

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.trace_provider.force_flush(timeout_millis=2_000)
            self.trace_provider.shutdown()
        except Exception:
            pass
        try:
            self.meter_provider.force_flush(timeout_millis=2_000)
            self.meter_provider.shutdown()
        except Exception:
            pass
