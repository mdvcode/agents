# Structured Output Guard

Agents write machine-readable artifacts. Those artifacts must be valid enough for future agents and scripts to trust.

## Required State
- One `.agent-runs/<run-id>/workflow.json`.
- Owned JSON artifacts under `.agent-runs/<run-id>/artifacts/`.
- `metrics.json`, raw events, and structured `errors.jsonl` in the same run.
- No mutable repository-root `artifacts/` mirror.

## Guard Rules
- JSON artifacts must parse.
- Required top-level fields must exist.
- Enumerated fields must use allowed values.
- Audit log entries must be JSON lines.
- Invalid structured output is a blocker until repaired or explicitly documented.

## Local Validation
Run:

```sh
make validate-artifacts RUN_ID=<run-id>
```

The validator is intentionally small and standard-library only.
