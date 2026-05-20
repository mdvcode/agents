# Agent Onboarding

Use this checklist before making changes.

## Entry Order
1. Read `AGENTS.md`.
2. Read `artifacts/lessons_learned.md`.
3. Read `docs/index.md`.
4. Identify the target project. If the user did not provide one and the task is project-specific, ask for it.
5. Read `docs/projects/README.md`.
6. Read `docs/projects/<project>/privacy.md` before touching project-specific issue memory.
7. Read `docs/wiki/index.md` and only the global wiki pages relevant to the task.
8. Read `docs/projects/<project>/wiki/` and `docs/projects/<project>/memory/` when the task belongs to a project.
9. Read the relevant map in `docs/graph/` and `docs/projects/<project>/graph/`.
10. Read the relevant kanban board in `docs/kanban/`.
11. For GitHub issue work, read or create `docs/projects/<project>/issues/issue-<number>.md`.
12. Read or create `artifacts/plan.md` using `docs/templates/goal.md` for non-trivial tasks.
13. Classify risk in `artifacts/risk.json`.

## Operating Loop
1. Inspect only the files needed for the task.
2. Write a short plan.
3. Patch minimally.
4. Run focused checks.
5. Run quality checks when available.
6. Run security checks when available.
7. Update artifacts.
8. Run `make validate-artifacts` or `make check`.
9. Update the project issue journal if the task belongs to a GitHub issue.
10. Update project wiki, memory, or graph when durable project knowledge changed.
11. Update global wiki, memory, or graph only for cross-project agent-system knowledge.
12. Update the relevant kanban card.
13. Append `artifacts/audit_log.jsonl`.
14. Leave the next action explicit.

## Handoff Rules
- If implementation is incomplete, leave a concrete blocker in `artifacts/report.md`.
- If checks fail because of the repository baseline, separate baseline failures from task-specific failures.
- If a lesson repeats, update `artifacts/lessons_learned.md`.
- If a task touches protected paths, stop at analysis and request human approval.
- Do not publish private project memory into a target project repository or PR unless the user explicitly approves a sanitized summary.

## What Good Looks Like
- The git diff is small.
- The current task is understandable from `artifacts/plan.md`, `artifacts/report.md`, the issue journal, and the kanban card.
- Durable new project knowledge is promoted from `artifacts/` into `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, or `docs/projects/<project>/graph/`.
- The repository contains the code output, docs, logs, and audit trail needed for review.
