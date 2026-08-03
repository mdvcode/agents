# Decision: Observe the outer loop

Date: 2026-08-03

## Status

Accepted for Milestone 3.

## Context

Harness logging recorded individual events, but operators could not correlate a queued task with its worker and workflow, quantify back pressure, distinguish unknown cost from zero cost, or inspect the engineering loop from one compact surface.

## Decision

- Use OpenTelemetry spans and metrics at worker, queue-derived, workflow, step, retry, and loop boundaries.
- Propagate W3C Trace Context from the worker into the workflow subprocess.
- Keep sanitized local JSONL spans as run-scoped evidence and support optional OTLP/HTTP exporters.
- Derive operational aggregates from authoritative run artifacts and scheduler state rather than create a second mutable state store.
- Show unknown cost explicitly and publish evidence coverage.
- Serve a data-free dashboard shell from the loopback control plane; keep operational APIs authenticated.
- Make telemetry fail-open and bounded so monitoring failure cannot stall task execution.

## Consequences

Operators can inspect queue pressure, worker state, latency, known costs, retries, loops, PR time, failures, interventions, and trace parentage without reading transcripts. The same evidence can feed Evaluation Framework comparisons. Hosted collectors, alert routing, retention management, and production deployment remain outside this slice.
