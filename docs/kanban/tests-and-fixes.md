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
- Step 1 harness: 101 tests passed with one optional smoke skipped in the ordinary suite; the separately enabled real Codex planner smoke passed. Contract validation, focused security, full-repository security, ownership rollback, missing-gate publication blocking, and diff checks passed.
- Test/fix board created.
- Verified JSON artifacts, artifact inventory, and `git diff --check`.
- Confirmed no Django application behavior changed.
- Added `make check`, `make security`, `make validate-artifacts`, and `make agent-status`.
