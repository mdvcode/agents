# Context Intelligence Platform

Context Intelligence Platform (CIP) is the only supported path from Harness knowledge to a model-backed role. It separates discovery, retrieval, budgeting, provenance, and memory lifecycle contracts so each concern can evolve independently.

## Stable API

```python
context = ContextEngine.build(task, repository, role, runtime)
```

`Context` contains a prompt-ready package, selected and excluded source records, token usage, and a structured provenance event. Workflow integration writes the package under `.agent-runs/<run-id>/context-manifests/packages/` and the append-only log under `.agent-runs/<run-id>/context-manifests/logs/`.

The runtime adapter reads only the compiled package. Manifest references to skills, artifacts, or source paths are provenance metadata and are not a second context-loading path. In particular, an agent never opens an Obsidian vault directly.

## Layers

1. Knowledge sources discover bounded static inputs: repository metadata, README/project docs, ADR/wiki, repository and Harness `AGENTS.md`, project profile, policies, role contracts, skills, current run artifacts, and configured Obsidian vaults.
2. `Retriever` ranks candidates. The current `RuleBasedRetriever` uses authority rules, task/role terms, topic aliases such as OAuth/authentication/security, and deterministic ordering.
3. `ContextBuilder` applies source priority and token/category budgets, truncates bounded chunks, and renders one Context Package.
4. Context logging records every selected/excluded source, score, rule, priority, original/included token estimate, truncation flag, and total budget usage.
5. `MemoryManager` defines `remember`, `forget`, `promote`, `archive`, `summarize`, and `retrieve`. It has no storage or learning implementation in this milestone.

## Knowledge taxonomy and priority

The top-level types are Documentation, Skills, Policies, Contracts, Memory, Project Profile, Repository, and current-task Artifact context. Documentation is further classified as README, ADR, Wiki, Obsidian, or general project documentation.

Context order is Task, current artifacts, Project Profile, Policies, Contracts, ADR, README, Skills, Memory, Repository intelligence, and additional docs. Memory has a reserved budget bucket but no default source until a MemPalace implementation is explicitly selected.

## Budgets

The builder uses a runtime-independent token estimate and a total token ceiling. Each category receives a configurable share, and one document cannot consume more than a configured share of its category. Unselected and truncated items remain visible in the context log, not silently discarded.

## Obsidian discovery

Vault roots may be supplied explicitly, configured in `AI_HARNESS_OBSIDIAN_VAULTS` using the platform path separator, or discovered when the active repository itself contains `.obsidian/`. Discovery reads bounded UTF-8 Markdown only. Symlinks, hidden paths, oversized files, and paths outside the resolved vault root are excluded.

## Extension boundary

Semantic, vector, or hybrid retrieval should implement the `Retriever` protocol and be injected into `ContextEngine`. Source discovery, Context Builder, manifest format, runtime boundary, and call sites remain unchanged. No embeddings, vector database, semantic search, learning loop, reflection, evaluation, model routing, or cost optimization are part of the current implementation.
