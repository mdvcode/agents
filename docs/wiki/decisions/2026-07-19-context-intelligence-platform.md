# Decision: Context Intelligence Platform boundary

Date: 2026-07-19

## Status

Accepted for the current Harness architecture.

## Context

The previous context compiler mixed a fixed policy reference list with direct runtime file reads and a separate BM25 private-memory helper. It bounded bytes, but it did not expose one knowledge-source contract, source taxonomy, category budget, or complete selected/excluded provenance. An agent could receive source files through several manifest arrays, so the compiler was not a strict context boundary.

## Decision

Introduce `ai_harness.context` as the single context layer:

- `ContextEngine` coordinates only interfaces and keeps `build(task, repository, role, runtime)` stable.
- Static `KnowledgeSource` implementations own bounded discovery, including Obsidian.
- `Retriever` is swappable; deterministic rule retrieval is the current backend.
- `ContextBuilder` owns priority, per-category allocation, truncation, and the total token ceiling.
- the workflow writes one compiled Context Package; runtime reads only that package;
- a structured context log records provenance and budget decisions;
- `MemoryManager` is an abstract MemPalace lifecycle interface and is not a storage or learning implementation.

The legacy `scripts/project_memory.py` helper remains available for compatibility and isolated tests, but it is no longer an implicit role-context input. Reintroducing memory retrieval requires a deliberate `MemoryManager` implementation and source adapter.

## Consequences

- Agents cannot bypass Context Engine to read Obsidian or other manifest references as supplied knowledge.
- Policies, role contracts, skills, project identity, ADRs, and docs are selected through the same observable pipeline.
- Context limits are expressed in tokens while retaining a final byte safety cap in the runtime adapter.
- A semantic or hybrid retriever can be added without changing Context Engine or runtime call sites.
- Memory learning, long-term storage, embeddings, model routing, reflection, evaluation, and optimization remain explicitly deferred.
