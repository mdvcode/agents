# Decision: Extend the dashboard for Adaptive Acceptance

Date: 2026-08-26

## Status

Accepted as an observability extension to Adaptive Execution v1.

## Context

The operational dashboard currently answers which tasks and workers need attention. Adaptive
Acceptance needs a second view: whether adaptive execution is measurably cheaper and faster than
the full workflow without a material quality regression or a missed mandatory gate.

## Decision

Extend the existing dashboard without replacing its control-plane architecture:

1. show a Full/Adaptive comparison;
2. show efficiency KPIs and their deltas;
3. show the persisted `ExecutionPlan` for an individual run;
4. distinguish executed, skipped, deterministic, and model-backed nodes;
5. show the selected model profile for each model-backed role;
6. show context-cache and token statistics;
7. show repair and escalation statistics;
8. read the authoritative `evals/adaptive_execution_acceptance.json` decision and its
   fingerprint-matched, run-scoped report;
9. render `PASS`, `FAIL`, or `NOT ENOUGH DATA` exactly from backend evidence;
10. expose no dashboard operation that can override acceptance, security, or approval policy.

The data flow is:

```text
Runs / Evals / Metrics
        |
        v
Adaptive Acceptance Evaluator
        |
        v
adaptive_execution_acceptance.json + fingerprinted report
        |
        v
Operational Metrics API
        |
        v
Dashboard
```

The evaluator owns comparison aggregates, threshold checks, and the acceptance verdict. The
dashboard may filter already-authoritative pair evidence for inspection, but it must not infer or
change acceptance.

## Consequences

- CLI, CI, automatic-mode selection, and the dashboard share one verdict.
- A missing, malformed, non-run-scoped, or fingerprint-mismatched report is displayed as
  `NOT ENOUGH DATA`; it is never treated as acceptance.
- Per-run diagnostics remain bounded and omit prompts, transcripts, source contents, and arbitrary
  report fields.
- The existing task controls, recovery, approvals, security gates, worktree isolation, and
  publication behavior are unchanged.

