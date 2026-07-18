# File Graph

## Core Policy
- `AGENTS.md` -> safety, verification, protected paths, artifact rules.
- `docs/onboarding.md` -> entry sequence for agents.
- `docs/index.md` -> documentation map.

## Active Work
- `.agent-runs/<run-id>/workflow.json` -> authoritative workflow state.
- `.agent-runs/<run-id>/artifacts/plan.md` -> current plan, owned by Planner.
- `.agent-runs/<run-id>/artifacts/risk.json` -> autonomy gates, owned by Risk Classifier.
- `.agent-runs/<run-id>/artifacts/quality.json` -> checks, owned by Quality Runner.
- `.agent-runs/<run-id>/artifacts/security.json` -> security gate, owned by Security Agent.
- `.agent-runs/<run-id>/artifacts/review.json` -> review gate, owned by Reviewer.
- `.agent-runs/<run-id>/artifacts/verdict.json` -> immutable pre-publication decision, owned by Orchestrator.
- `.agent-runs/<run-id>/artifacts/publication.json` -> commit/push/PR state, owned by Publication.
- `.agent-runs/<run-id>/audit-log.jsonl` -> append-only publication action history.

## Durable Memory
- `docs/projects/<project>/issues/` -> private per-project issue histories.
- `docs/projects/<project>/wiki/` -> private project curated knowledge.
- `docs/projects/<project>/memory/` -> private project long-term and topic memory.
- `docs/projects/<project>/graph/` -> private project maps.
- `docs/wiki/` -> global agent-system curated knowledge.
- `docs/memory/` -> global agent-system long-term, daily, scratchpad, and topic memory.
- `docs/kanban/` -> global boards.
- `docs/graph/` -> global agent-system maps.
- `scripts/project_memory.py` -> scoped Markdown chunking, BM25 ranking, and run-local retrieval context generation.
- `.agent-runs/<run-id>/context-manifests/retrieved/<role>.md` -> ephemeral retrieved chunks with provenance for one role execution.
