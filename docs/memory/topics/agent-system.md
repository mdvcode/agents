# Topic: Agent System

## 2026-05-19
- The workspace now separates active artifacts from durable memory.
- `docs/wiki/` is the curated knowledge layer.
- `docs/memory/` is the long-term memory layer.
- `docs/issues/` is per-issue execution history.
- `schemas/` plus `scripts/validate_artifacts.py` provide local guardrails for structured artifacts.

## 2026-07-12
- Role context manifests retrieve relevant Markdown sections from the active memory scope with local BM25, preserve provenance, and enforce result and byte budgets.
