# Observability

AI Harness observability makes the outer engineering loop answerable: what entered the queue, which worker owned it, what the workflow attempted, what evidence it produced, how long it took, and where human intervention was required.

## Run it

```bash
make control-plane
open http://127.0.0.1:8765/dashboard
```

`make dashboard` is an alias for the same loopback server. If `AGENT_CONTROL_PLANE_TOKEN` is set, enter it in the dashboard; the token stays in browser session storage. The public HTML shell contains no run data. `/metrics`, `/traces`, and the other operational JSON endpoints retain bearer-token enforcement.

For a machine-readable snapshot:

```bash
make metrics
python3 scripts/operational_metrics.py --output /tmp/harness-observability.json
```

## Signals

The snapshot and dashboard expose:

- worker health, stalled workers, current assignments, and restarts;
- queued, active, completed, blocked, and dead-letter tasks;
- queue wait, total task, workflow, span, and PR-time distributions;
- known USD cost plus known/unknown coverage, never an invented zero;
- queue and workflow retries, loop iterations, failures, and human interventions;
- recent sanitized spans with trace and parent IDs.

The operational snapshot contract is `schemas/observability_snapshot.schema.json`. Local spans use `schemas/otel_span.schema.json` and are stored at `.agent-runs/<run-id>/raw-events/otel-spans.jsonl`.

## OpenTelemetry

The worker starts `ai_harness.worker.task`, injects W3C Trace Context into the workflow subprocess, and the workflow creates `ai_harness.workflow` plus child `ai_harness.workflow.step` spans. Metrics use OpenTelemetry counters and histograms for tasks, retries, loop iterations, failures, and duration.

The runtime works without a collector. Configure standard OpenTelemetry environment variables to export over OTLP/HTTP:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_SERVICE_NAME=ai-harness
make queue-worker
```

`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` may be configured separately. Exporter setup and delivery are fail-open: loss of monitoring cannot fail an engineering workflow.

## Privacy and bounds

Only operational metadata is exported. The local exporter rejects content-bearing attribute names such as prompts, goals, stdout, stderr, authorization, secrets, passwords, and tokens. It does not record exception messages or stack traces. String and collection sizes are bounded.

The trace reader tails at most 1 MiB from each of the 100 newest non-symlink span files and returns at most 200 spans. The dashboard renders values with DOM `textContent`, not HTML interpolation. It is restricted to the existing loopback control plane and sends a restrictive Content Security Policy.

## Interpretation

Telemetry is evidence, not a verdict. A lower latency with missing cost or failure evidence is not an improvement. Use the Evaluation Framework to compare frozen variants, and use observability to explain the result and find the next constrained change.

This follows the factory model described in [Software Factories, Light and Dark](https://addyosmani.com/blog/software-factories/) and the human-owned evidence and verdict loop in [Own the Outer Loop](https://addyo.substack.com/p/own-the-outer-loop). The implementation follows the official [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/) and [exporter](https://opentelemetry.io/docs/languages/python/exporters/) guidance.
