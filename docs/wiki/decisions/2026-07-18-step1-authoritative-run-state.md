# Decision: One Authoritative Run State

## Status
Accepted on 2026-07-18.

## Context
The harness previously stored task outputs both in repository-root `artifacts/` and in `.agent-runs/<run-id>/`. Publication also copied its state to the run root and rewrote the Orchestrator-owned verdict. Those mirrors made resume, audit, ownership, and failure recovery ambiguous.

## Decision
All mutable state for one task lives under `.agent-runs/<run-id>/`:

```text
workflow.json
context-manifests/
role-requests/
role-results/
raw-events/
artifacts/
metrics.json
errors.jsonl
audit-log.jsonl
```

Artifact ownership is declared in `.agent-artifact-owners.yaml` and enforced against `.agent-role-contracts.yaml`. A role may create or change only its owned artifacts. `verdict.json` is an immutable pre-publication decision owned by Orchestrator; `publication.json` is the only commit, push, and PR state and is owned by Publication.

Repository-root `artifacts/` is not a compatibility path and must not be recreated.

## Consequences
- Resume and idempotency use one run directory and one publication fingerprint.
- Raw Codex events and token usage remain auditable beside the task state.
- HIGH risk stops before implementation publication mutations.
- LOW/MEDIUM publication does not mutate the earlier verdict.
- Step 1 acceptance requires a selected 10-20 run evidence manifest verified by `make step1-verify`.
