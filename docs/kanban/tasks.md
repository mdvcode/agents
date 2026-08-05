# Tasks Kanban

## Backlog
- Add automation later to create an issue branch and issue journal from one command.

## Ready
- Keep `.agents/prompts/` aligned with the docs/onboarding flow.

## In Progress
- Step 1 evidence series: implementation and real Codex smoke are complete; collect and verify 10-20 real task runs against a disposable publication target.
- Step 2 production evidence: implementation is complete; run at least three real queued tasks with two overlapping workers, one successful PR, one human exception, and UI evidence in a registered web repository.

## Review
- Milestone PR1 production runtime: every locally verifiable requirement, all 20 chaos scenarios, runtime preflight, and real Codex smoke are complete; only the user-excluded real 30-task multi-hour soak remains the production-readiness gate.

## Done
- Current-branch task mode: safe queued execution in the clean already checked-out branch without creating a task worktree, with stable-branch validation, recovery identity, and per-checkout serialization.
- Milestone R1 completion hardening: publication crash reconciliation, atomic approval replay, owned-artifact repair, fail-open workflow telemetry, structured control failures, recovery-rich CLI status, and direct regression coverage.
- Milestone R1 — Unified Task Recovery Layer: deterministic failure policy, bounded retry/repair/resume, queue recovery states, worker/service isolation, checkpoints, idempotency, CLI/observability, and failure-injection coverage.
- Qdrant Content Manager bonuses: thin service-layer routing, a reproducible five-service Docker Compose stack, isolated Qdrant tests, live smoke verification, and three local commits; no publication.
- Qdrant Content Manager coding challenge: transactional PATCH, read-only consistency diagnostics, regression tests, and two local commits on `test_api_func`; no publication.
- Context Intelligence Platform: compiled-only static knowledge sources, rule retrieval, token budgets, provenance logging, Obsidian discovery, and the MemPalace interface behind Context Engine.
- AI Harness UX milestone: pip/pipx packaging, product CLI, local project configuration, idempotent task intake, compact status, and doctor diagnostics.
- Step 2 operational control plane: scoped approval/resume API, crash checkpoint recovery, signed CI feedback ingestion, registered worker daemon lifecycle, universal task intake, and compact operational metrics API.
- Step 2 implementation: deterministic routing, four bounded repair loops, independent verifier contracts, same-worktree publication, SQLite workers, exception CLI, tool governance, and acceptance verifier.
- Security severity routing and explicit non-LLM Issue Intake harness-stage classification.
- Step 1 implementation: authoritative run state, artifact ownership, structured errors, metrics, publication idempotency, default-branch safety, and series verifier.
- P3.1e: real Codex preflight and smoke pass with the application-bundled CLI.
- Generated and verified `Daryna_Barabanova_CV_ATS.pdf` with embedded TrueType fonts and three-parser ATS extraction checks.
- P4.1: RAG-powered project memory retrieval.
- P3.2: deterministic dynamic routing and bounded repair loops.
- P3.1f: remove obsolete project coupling from the agent control plane.
- P3.1d: production Codex execution path and role trust boundaries implemented and verified.
- P1.1: Harden autonomy and project profile contracts.
- P1: Add project profiles for agent_workspace, Django, and a generic Next.js/web profile.
- P0: Normalize agent autonomy contract, publication policy, and artifact schemas.
- Git repository exists for agent workspace history.
- Durable docs store created under `docs/`.
- Kanban boards created under `docs/kanban/`.
- Onboarding path documented for new agents.
- Agent workspace cleanup and onboarding docs completed.
- Per-issue history convention added under `docs/issues/`.
- PDF-derived wiki, memory, graph, goal template, schemas, Makefile, and agent skills added.
