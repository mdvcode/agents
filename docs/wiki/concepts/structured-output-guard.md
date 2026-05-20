# Structured Output Guard

Agents write machine-readable artifacts. Those artifacts must be valid enough for future agents and scripts to trust.

## Required Artifacts
- `artifacts/risk.json`
- `artifacts/quality.json`
- `artifacts/verdict.json`
- `artifacts/audit_log.jsonl`

## Guard Rules
- JSON artifacts must parse.
- Required top-level fields must exist.
- Enumerated fields must use allowed values.
- Audit log entries must be JSON lines.
- Invalid structured output is a blocker until repaired or explicitly documented.

## Local Validation
Run:

```sh
make validate-artifacts
```

The validator is intentionally small and standard-library only.
