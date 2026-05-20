# Summary
- Corrected the agent workspace for multiple target projects and private project memory.
- `/Users/user/agents` is now explicitly the private control plane.
- Private issue execution history and project memory are no longer modeled as global `docs/issues` memory; they live under `docs/projects/<project>/`.

# Agent Improvements
- Added `docs/wiki/` for LLM Wiki-style durable knowledge.
- Added `docs/memory/` for long-term memory, scratchpad, daily notes, and topic memory.
- Added `docs/graph/` for file, workflow, risk, and agent maps.
- Added `docs/templates/goal.md` for the `/goal` structure from the PDF.
- Added artifact schemas in `schemas/` and a local validator in `scripts/validate_artifacts.py`.
- Added a `Makefile` with `check`, `security`, `validate-artifacts`, and `agent-status`.
- Added skills for issue intake, context engineering, structured output guards, performance optimization, and documentation/ADRs.
- Updated issue journals with checkpoints and trace.
- Added `docs/projects/` with a reusable project template and `privacy.md`.
- Updated issue intake, context engineering, and documentation skills to require project identification and privacy review.

# GitHub Issue Flow
- Each GitHub issue gets its own branch, for example `codex/issue-123-short-name`.
- Each issue gets its own private durable journal, for example `docs/projects/contact-api/issues/issue-123.md`.
- `artifacts/` stays focused on the active task and can be cleaned after the issue summary is copied into the journal.
- Stable project knowledge from finished issues should be promoted into `docs/projects/<project>/wiki/` or `docs/projects/<project>/memory/`.
- Global `docs/wiki/` and `docs/memory/` are for cross-project agent-system knowledge only.

# Confidentiality
- Private project memory stays local by default.
- Target project repositories receive only reviewed code, tests, approved public docs, and sanitized PR summaries.
- Agents do not start solving new GitHub issues automatically when they appear; work starts when the user gives a project and issue number unless a separate monitoring automation is requested.

# Verification
- Passed: `git status --short` inspection. It shows this task plus pre-existing staged `.idea/*` files that were not modified.
- Passed: `find artifacts -maxdepth 1 -type f | sort`. Only required artifacts remain.
- Passed: JSON parsing for `artifacts/risk.json`, `artifacts/quality.json`, and `artifacts/verdict.json`.
- Passed: `python3 scripts/validate_artifacts.py`.
- Passed: `git diff --check`.
- Passed: `make check`.
- Passed: `make security`.
- Passed: `make agent-status` with non-fatal macOS xcrun cache warnings in the read-only sandbox.

# Next Action
- For each real project, copy `docs/projects/_template/` to `docs/projects/<project>/` and fill `privacy.md`.
- For the next real GitHub issue, provide project + issue number; then create `docs/projects/<project>/issues/issue-<number>.md`.
