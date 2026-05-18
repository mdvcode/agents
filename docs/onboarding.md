# Agent Onboarding

Use this checklist before making changes.

## Entry Order
1. Read `AGENTS.md`.
2. Read `artifacts/lessons_learned.md`.
3. Read `docs/index.md`.
4. Read the relevant kanban board in `docs/kanban/`.
5. For GitHub issue work, read or create `docs/issues/issue-<number>.md`.
6. Read or create `artifacts/plan.md`.
7. Classify risk in `artifacts/risk.json`.

## Operating Loop
1. Inspect only the files needed for the task.
2. Write a short plan.
3. Patch minimally.
4. Run focused checks.
5. Run quality checks when available.
6. Run security checks when available.
7. Update artifacts.
8. Update the issue journal if the task belongs to a GitHub issue.
9. Update the relevant kanban card.
10. Append `artifacts/audit_log.jsonl`.
11. Leave the next action explicit.

## Handoff Rules
- If implementation is incomplete, leave a concrete blocker in `artifacts/report.md`.
- If checks fail because of the repository baseline, separate baseline failures from task-specific failures.
- If a lesson repeats, update `artifacts/lessons_learned.md`.
- If a task touches protected paths, stop at analysis and request human approval.

## What Good Looks Like
- The git diff is small.
- The current task is understandable from `artifacts/plan.md`, `artifacts/report.md`, the issue journal, and the kanban card.
- The repository contains the code output, docs, logs, and audit trail needed for review.
