# Agent Wiki

This is the compounding knowledge layer for the agent workspace.

Unlike `artifacts/`, this directory is not cleared between tasks. Agents update it when a task produces durable knowledge that should improve future work.

## Pages
- `concepts/context-intelligence-platform.md`: the single knowledge-to-context boundary, static sources, rule retrieval, budgets, provenance, and memory interface.
- `concepts/agent-memory.md`: layered memory architecture and local RAG retrieval for role context.
- `concepts/goal-prompt.md`: `/goal` structure for scoping tasks.
- `concepts/llm-wiki.md`: persistent wiki pattern from the PDF.
- `concepts/structured-output-guard.md`: schema and repair expectations for agent outputs.
- `concepts/token-hygiene.md`: how agents avoid wasting context.
- `entities/agent-workspace.md`: map of this local repository.
- `decisions/2026-05-19-pdf-agent-system-upgrade.md`: decision record for the PDF-driven upgrade.
- `decisions/2026-07-18-step1-authoritative-run-state.md`: single-run state, artifact ownership, and Step 1 acceptance contract.
- `decisions/2026-07-18-step2-deterministic-concurrency.md`: authoritative routing, bounded repairs, verifier plane, worker queue, and tool governance.
- `decisions/2026-07-18-step2-operational-control-plane.md`: approval/resume, recovery, event and CI ingestion, worker service lifecycle, and metrics API.
- `decisions/2026-07-19-runtime-abstraction.md`: provider-neutral Runtime contract, the sole Step 2 Codex CLI runtime, and the deferred adapter/router roadmap.
- `decisions/2026-07-19-ai-harness-ux.md`: installable `agent` CLI, project-local onboarding, and the boundary between local execution identity and central publication authority.
- `decisions/2026-07-19-context-intelligence-platform.md`: compiled-only role context, swappable retrieval, token budgets, context logs, and deferred MemPalace storage.
- `decisions/2026-08-03-evaluation-framework.md`: frozen eval inputs, deterministic run scoring, explicit evidence coverage, compatible comparisons, and clean controls.
- `decisions/2026-08-03-observability-platform.md`: outer-loop tracing, operational metrics, sanitized local evidence, optional OTLP export, and loopback dashboard.
- `decisions/2026-08-03-production-evaluation-corpus.md`: version-2 deterministic corpus, candidate-independent fingerprints, non-compensating critical metrics, frozen baseline, and CI gate.
- `contradictions.md`: claims or practices that need reconciliation.

## Update Rules
- Add durable knowledge here, not raw logs.
- Prefer short pages with links to source issue journals.
- Mark contradictions instead of silently overwriting older claims.
- Keep raw task evidence in `docs/issues/` or `artifacts/`, then summarize it here.
