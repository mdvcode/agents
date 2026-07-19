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
- `.agent-runs/<run-id>/raw-events/tool-calls.jsonl` -> sanitized tool authorization and execution decisions.
- `.agent-queue/tasks.db` -> scheduler-only leases, heartbeats, retries, and dead-letter state; never task artifact state.
- `.agent-routing.yaml` -> deterministic gate and bounded-loop policy.
- `.agent-tool-policy.yaml` -> role/action/domain/credential/timeout tool authority.

## Durable Memory
- `docs/projects/<project>/issues/` -> private per-project issue histories.
- `docs/projects/<project>/wiki/` -> private project curated knowledge.
- `docs/projects/<project>/memory/` -> private project long-term and topic memory.
- `docs/projects/<project>/graph/` -> private project maps.
- `docs/wiki/` -> global agent-system curated knowledge.
- `docs/memory/` -> global agent-system long-term, daily, scratchpad, and topic memory.
- `docs/kanban/` -> global boards.
- `docs/graph/` -> global agent-system maps.
- `ai_harness/context/` -> Context Engine, static knowledge sources, retrieval protocol, rule retriever, token-budgeted builder, context logs, and MemPalace interface.
- `scripts/context_compiler.py` -> workflow compatibility facade that writes Context Engine packages and manifests.
- `scripts/project_memory.py` -> legacy scoped BM25 compatibility helper; not an implicit Context Engine source.
- `.agent-runs/<run-id>/context-manifests/retrieved/<role>.md` -> ephemeral retrieved chunks with provenance for one role execution.
