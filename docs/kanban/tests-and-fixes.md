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
- Hang-boundary regressions cover large unread stdin, workflow and fast-mode remaining deadlines, expired recovery dead-lettering, approval-expiry queue synchronization, descendant termination, prompt grace return, bounded watch, and immediate missing-worker diagnosis; runtime-chaos, the full suite, and real Codex smoke pass.
- Dashboard verification: three focused control tests, wheel inspection, security scan, and desktop/mobile browser checks with no console errors or horizontal overflow.
- Task-branch resilience covers long security prompts, bounded automatic names, Git-valid Unicode and punctuation in existing branches, and actionable rejection of genuinely unsafe refs.
- Ordinary-user install/update verification: installer syntax, executable mode, no-sudo behavior, paths with spaces, pipx boundary, ignored local setup, safe Git update refusal, package refresh, and worker restart have focused regression coverage.
- Task-attention regressions cover exact worker reason preservation, status/watch commands, run-bound answers, approval separation, prompt injection of prior answers, non-empty attention details, and immediate retry suppression after authoritative pause.
- Startup reliability verification: 305 repository tests passed with one opt-in smoke skipped in the ordinary suite; the separately enabled real Codex smoke, runtime preflight, pipx dependency check, security scan, fresh-project init/doctor/start/task/status smoke, and diff checks passed.
- Production Runtime PR1 local acceptance: 86 focused runtime-chaos tests, 287 repository tests, real Codex preflight/smoke, the 30-case evaluation gate, security, artifacts, compilation, and diff checks passed; only the explicitly excluded multi-hour production soak remains.
- Milestone E2 verification: 41 focused tests and the 218-test repository suite passed with one opt-in smoke skipped; regression failure, incompatible dataset/scorer rejection, structured-input safety, contracts, security, wheel assets, and diff checks passed.
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
