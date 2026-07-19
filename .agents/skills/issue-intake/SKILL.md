---
name: issue-intake
description: "Turn an issue or task into a traceable local work unit."
---
# Issue Intake Skill

## Purpose
Turn a GitHub issue into a traceable local work unit.

## Workflow
1. Read `AGENTS.md`, `docs/onboarding.md`, and `docs/issues/README.md`.
2. Identify the target project. If unknown, ask the user before proceeding.
3. Read `docs/projects/<project>/privacy.md`.
4. Fetch or read the GitHub issue.
5. Create or identify branch `issue/<number>-<short-name>`.
6. Copy `docs/issues/_template.md` to `docs/projects/<project>/issues/issue-<number>.md` if it does not exist.
7. Fill links, status, risk, goal, scope, and first timeline entry.
8. Add or update a concise kanban card in `docs/kanban/tasks.md`.
9. Create one `.agent-runs/<run-id>/` and write its `artifacts/plan.md` using the `/goal` structure.

## Output
- Updated issue journal.
- Updated kanban card.
- Updated run-scoped `artifacts/plan.md`.
- Confirmed project privacy policy was read.

## Stop Rules
- Stop if the issue touches protected paths or cannot be understood from available context.
- Stop if the target project is unknown.
