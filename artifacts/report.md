# Summary
- Analyzed the local agent workspace.
- Found a strong role split in `.agents/prompts/` and `.agents/skills/`, but the workspace lacked a clear onboarding entry, durable docs index, kanban boards, and artifact cleanup rules.
- Added the missing structure so new agents can enter the repository consistently and understand where tasks, tests/fixes, features, docs, logs, issue histories, and runtime artifacts live.

# Agent Improvements
- `AGENTS.md` now defines the agent workspace model.
- `docs/onboarding.md` gives every agent the same entry checklist.
- `docs/git-and-logs.md` explains git, docs, logs, and artifact responsibilities.
- `docs/agent-system.md` records the analysis and recommended next improvements.
- `docs/kanban/tasks.md`, `docs/kanban/tests-and-fixes.md`, and `docs/kanban/features.md` implement the requested kanban layout.
- `docs/issues/README.md` and `docs/issues/_template.md` define durable history per GitHub issue and branch.

# Artifact Cleanup
- Removed stale one-off probe scripts, large JSON outputs, sweep reports, rollback/smoke leftovers, and obsolete suggested tests from `artifacts/`.
- Preserved required current-task artifacts, `artifacts/lessons_learned.md`, and `artifacts/audit_log.jsonl`.

# GitHub Issue Flow
- Each GitHub issue gets its own branch, for example `codex/issue-123-short-name`.
- Each issue gets its own durable journal, for example `docs/issues/issue-123.md`.
- `artifacts/` stays focused on the active task and can be cleaned after the issue summary is copied into the journal.

# Verification
- Passed: `git status --short` inspection. It shows this task plus pre-existing staged `.idea/*` files that were not modified.
- Passed: `find artifacts -maxdepth 1 -type f | sort`. Only required artifacts remain.
- Passed: JSON parsing for `artifacts/risk.json`, `artifacts/quality.json`, and `artifacts/verdict.json`.
- Passed: `git diff --check`.
- Blocked: `make check` and `make security` are unavailable because this repository has no matching Makefile targets.

# Next Action
- Review the docs/artifact cleanup.
- Optionally add `make check` and `make security` targets for this agent workspace so future agents can run the full required pipeline here.
