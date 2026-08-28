# File Graph

## Core Policy
- `AGENTS.md` -> safety, verification, protected paths, artifact rules.
- `docs/onboarding.md` -> entry sequence for agents.
- `docs/index.md` -> documentation map.

## Active Work
- The installed Harness home is authoritative for production execution. Matching directories in a source checkout are development state unless that checkout is the active installed home.
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

## File Lifecycle
- Required and tracked: `ai_harness/`, `scripts/`, `schemas/`, `.agents/`, tests, policy YAML, packaging files, and reviewed docs/evals.
- Target-local identity: `.agent/project.yaml`; it grants execution identity only and may remain ignored.
- Recoverable operational state: `.agent-runs/`, `.agent-queue/`, `.agent-worktrees/`, and `.agent-cache/`; ignored, never publication input, retained while active or needed for recovery.
- Re-creatable output: `.venv/`, build/dist/egg-info, Python/test caches, `tmp/`, and `output/` after evidence is summarized.
- Unneeded metadata: `.DS_Store` and editor-local state.

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
- `evals/` -> versioned datasets, benchmarks, golden tasks, regression taxonomy, and rubrics.
- `ai_harness/evaluation/` -> deterministic evidence collection, scoring, dataset assertions, comparison, and leaderboard logic.
- `scripts/run_evals.py`, `scripts/score.py`, `scripts/compare_runs.py`, `scripts/leaderboard.py` -> Evaluation Framework CLI boundary.
- `ai_harness/evaluation/corpus.py`, `scripts/eval_regression.py` -> production corpus scorers, candidate-independent fingerprints, frozen-baseline comparison, and CI gate.
- `evals/baselines/`, `evals/experiments/` -> reviewed baseline evidence and explicit experiment thresholds.
- `ai_harness/observability/` -> OpenTelemetry providers, sanitized JSONL span export, bounded trace reads, and dashboard shell.
- `scripts/operational_metrics.py` -> authoritative run + scheduler aggregation for workers, queue, latency, cost coverage, retries, loops, PR time, failures, and traces.
- `scripts/control_plane_api.py` -> authenticated operational APIs and the data-free loopback dashboard route.
- `schemas/otel_span.schema.json`, `schemas/observability_snapshot.schema.json` -> observability evidence contracts.
- `.agent-runs/<run-id>/context-manifests/retrieved/<role>.md` -> ephemeral retrieved chunks with provenance for one role execution.
