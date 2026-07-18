# Decision: Deterministic Concurrent Workflows

## Status

Accepted on 2026-07-18. Production acceptance remains evidence-gated.

## Context

Model-suggested `next_action` values cannot safely control gates, retries, publication, or concurrent task ownership. A concurrent runner also needs bounded recovery, independent verification, one worktree per task, a human exception surface, and explicit tool authority.

## Decision

- `scripts/workflow_router.py` is authoritative. Role `next_action` values are advisory only. Routing policy lives in `.agent-routing.yaml`, and every decision must validate against `schemas/workflow_route.schema.json`.
- Security findings are routed by explicit `highest_severity`: `critical` hard-blocks with structured state, while `medium` and `high` require human approval.
- Issue Intake is declared as a deterministic `harness_stage` with `llm_invocation=false`; its checkpoint records that no model was invoked.
- Quality, review, CI, and frontend verification repairs have independent iteration, token, and time limits. The router compares both failure and diff fingerprints and stops when the same failure repeats without progress.
- Security, review, architecture consistency, semantic conflict, and frontend/user-flow verification are read-only roles using the shared `works | broken | unavailable` verifier contract. A UI `works` verdict requires real loopback Playwright evidence.
- Task Intake creates the task worktree. Implementation, verification, commit, push, and PR all use that exact worktree. Publication refuses a repository or branch that differs from `workflow.json`.
- `.agent-queue/tasks.db` is scheduler state, not task workflow state. SQLite leases, heartbeats, retries, dead-letter status, and idempotent task keys coordinate 2–3 workers. Each task's authoritative mutable workflow state remains only in `.agent-runs/<run-id>/`.
- `scripts/list_runs.py` is the compact exception surface. Humans review blocked runs, approvals, exhausted budgets, repeated failures, security stops, missing UI evidence, stalled workers, and dead letters instead of transcripts.
- `.agent-tool-policy.yaml` is the tool authority contract. It controls role, action, side effect, domain, credential type, and timeout. Decisions are recorded without credential values in `raw-events/tool-calls.jsonl`.

## Acceptance

Unit and integration tests prove deterministic behavior, but do not close production acceptance. `make step2-verify RUN_ID=<evidence-run> QUEUE_DB=<queue.db>` must pass against at least three real terminal queued tasks, at least two workers with measured overlap, isolated worktrees, real Codex/token/tool traces, one successful PR, and one human exception. UI acceptance additionally needs a registered web repository with a runnable local development environment.

## Consequences

- A role cannot bypass a gate by naming publication.
- Repair loops stop predictably instead of consuming unlimited time or tokens.
- Publication cannot create a second unrelated worktree.
- Queue failures remain recoverable and visible without weakening per-run state ownership.
- Step 2 cannot be reported as closed from synthetic fixtures alone.
