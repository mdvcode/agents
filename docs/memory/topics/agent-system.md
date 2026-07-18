# Topic: Agent System

## 2026-05-19
- The workspace now separates active artifacts from durable memory.
- `docs/wiki/` is the curated knowledge layer.
- `docs/memory/` is the long-term memory layer.
- `docs/issues/` is per-issue execution history.
- `schemas/` plus `scripts/validate_artifacts.py` provide local guardrails for structured artifacts.

## 2026-07-12
- Role context manifests retrieve relevant Markdown sections from the active memory scope with local BM25, preserve provenance, and enforce result and byte budgets.

## 2026-07-18
- Mutable task state is authoritative only under `.agent-runs/<run-id>/`; the root `artifacts/` mirror was removed.
- Artifact ownership is explicit and enforced. Orchestrator owns the immutable verdict; Publication owns commit, push, and PR state.
- Step 1 evidence is a 10-20 task series verified for real Codex provenance, gate completeness, HIGH stops, PR publication, idempotency, secret safety, default-branch safety, and structured errors.
- Step 2 uses an authoritative deterministic router, bounded fingerprinted repair loops, read-only independent verifiers, one task worktree through PR, an SQLite leased worker pool, a compact exception queue, and declarative tool governance.
- Queue state is scheduler-only. A task remains authoritative under its `.agent-runs/<run-id>/`, and production acceptance requires real concurrent evidence rather than synthetic fixtures.
