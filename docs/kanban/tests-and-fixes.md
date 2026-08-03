# Tests And Fixes Kanban

## Backlog
- Create a reusable focused-check recipe for documentation-only changes.
- Document known baseline failures separately when a Django task exposes them.

## Ready
- None.

## In Progress
- None.

## Review
- None.

## Done
- Observability verification: 42 focused tests and 205-test repository suite passed with one opt-in smoke skipped; contract, security, wheel, diff, authenticated desktop dashboard, responsive 390 px layout, and console checks passed.
- Evaluation Framework verification: 7 focused behavior tests, 31 eval/validator/packaging tests, wheel inspection, CLI smoke, security scan, and 201-test repository suite passed; one opt-in real Codex smoke remained skipped.
- Step 2 focused tests cover route authority, bounded loop progress, verifier contracts, worktree publication identity, queue leases/retries/dead letters, worker concurrency, exception filtering, tool policy, and evidence evaluation.
- Regression coverage proves CRITICAL security hard-blocks with structured state, MEDIUM security requests approval, and Issue Intake never invokes an LLM adapter.
- Operational regression coverage proves exact approval scope and one-time consumption, API approve/resume, normalized multi-source intake, signed/redacted CI repair ingestion, worker registration/stall detection, and expired-lease checkpoint resume.
- Step 1 harness: 101 tests passed with one optional smoke skipped in the ordinary suite; the separately enabled real Codex planner smoke passed. Contract validation, focused security, full-repository security, ownership rollback, missing-gate publication blocking, and diff checks passed.
- Test/fix board created.
- Verified JSON artifacts, artifact inventory, and `git diff --check`.
- Confirmed no Django application behavior changed.
- Added `make check`, `make security`, `make validate-artifacts`, and `make agent-status`.
