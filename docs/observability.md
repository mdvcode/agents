# Observability

AI Harness observability makes the outer engineering loop answerable: what entered the queue, which worker owned it, what the workflow attempted, what evidence it produced, how long it took, and where human intervention was required.

## Run it

For ordinary use, open the task-control dashboard from an initialized project:

```bash
agent dashboard
```

This opens an authenticated local page that can launch tasks through the same worker as `agent task`, show live queue/worker/run state, surface bounded questions with recommended dropdown choices and a custom-answer fallback, and answer, approve, retry, or abort a run through the same product CLI policy boundary. Dashboard history cleanup is a reversible browser-local presentation preference: it hides only completed/cancelled rows and never deletes queue records, task events, run artifacts, active work, or tasks needing attention. It binds only to loopback. The generated token stays in the browser session and is removed from the visible URL after startup.

The lower-level contributor command remains available:

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
- recovery attempts, successes, exhausted budgets, task retries, output repairs, successful resumes, worker crashes, and recovery actions;
- recent sanitized spans with trace and parent IDs.

CLI status also prints queue-task age. `agent watch` distinguishes an unavailable worker from active execution immediately, so a retained queued task is not presented as a silent running hang. Runtime, workflow, recovery, and approval limits are summarized in the operator runbook.

The operational snapshot contract is `schemas/observability_snapshot.schema.json`. Local spans use `schemas/otel_span.schema.json` and are stored at `.agent-runs/<run-id>/raw-events/otel-spans.jsonl`.

## OpenTelemetry

The worker starts `ai_harness.worker.task`, injects W3C Trace Context into the workflow subprocess, and the workflow creates `ai_harness.workflow` plus child `ai_harness.workflow.step` spans. Role execution adds `ai_harness.runtime.execute` and `.timeout`; recovery adds `ai_harness.recovery.classify`, `.retry`, `.repair`, `.resume`, and `.dead_letter`. An isolated worker failure emits `ai_harness.worker.crash`, and every publication reconciliation emits `ai_harness.publication.idempotency_check` with only the names of prevented side-effect steps. Metrics include `runtime_executions_total`, `runtime_failures_total`, `runtime_timeouts_total`, `recovery_attempts_total`, `recovery_success_total`, `recovery_exhausted_total`, `task_retries_total`, `output_repairs_total`, `resume_attempts_total`, `resume_success_total`, `worker_crashes_total`, `dead_letters_total`, `duplicate_side_effects_prevented_total`, and `queue_lease_expirations_total` alongside task, loop, failure, and duration signals. These counters are emitted on their authoritative runtime, queue, worker, or publication transitions rather than inferred only for the dashboard.

For live liveness, `.agent-runs/<run-id>/progress.json` contains the current phase, latest SDK event, active tool, last progress timestamp, token budget usage, stop reason, and run-bound thread id. The bounded SDK event stream is kept in `raw-events/sdk-events.jsonl`. These files contain event metadata, not credentials, and are used by status/watch and idle detection instead of inferring progress from final stdout.

The runtime works without a collector. Configure standard OpenTelemetry environment variables to export over OTLP/HTTP:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_SERVICE_NAME=ai-harness
make queue-worker
```

`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` may be configured separately. Exporter setup and delivery are fail-open: loss of monitoring produces a content-free runtime warning but cannot fail an engineering workflow.

## Privacy and bounds

Only operational metadata is exported. The local exporter rejects content-bearing attribute names such as prompts, goals, stdout, stderr, authorization, secrets, passwords, and tokens. It does not record exception messages or stack traces. String and collection sizes are bounded.

The trace reader tails at most 1 MiB from each of the 100 newest non-symlink span files and returns at most 200 spans. The dashboard renders values with DOM `textContent`, not HTML interpolation. It is restricted to the existing loopback control plane and sends a restrictive Content Security Policy.

## Interpretation

Telemetry is evidence, not a verdict. A lower latency with missing cost or failure evidence is not an improvement. Use the Evaluation Framework to compare frozen variants, and use observability to explain the result and find the next constrained change.

This follows the factory model described in [Software Factories, Light and Dark](https://addyosmani.com/blog/software-factories/) and the human-owned evidence and verdict loop in [Own the Outer Loop](https://addyo.substack.com/p/own-the-outer-loop). The implementation follows the official [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/) and [exporter](https://opentelemetry.io/docs/languages/python/exporters/) guidance.
