# Issue History

Use project-scoped issue journals for durable execution history per GitHub issue.

This top-level directory only stores the shared template and rules. Real issue journals should live under `docs/projects/<project>/issues/`.

## Naming
- Journal file: `docs/projects/<project>/issues/issue-<number>.md`
- Branch name: `codex/issue-<number>-<short-name>`
- Kanban card: one short card in `docs/kanban/tasks.md`

Example:
- GitHub issue: `#123`
- Branch: `codex/issue-123-fix-contact-export`
- Project: `contact-api`
- Journal: `docs/projects/contact-api/issues/issue-123.md`

## Workflow
1. Create or switch to the issue branch.
2. Read `docs/projects/<project>/privacy.md`.
3. Copy `docs/issues/_template.md` to `docs/projects/<project>/issues/issue-<number>.md`.
4. Fill in issue URL, branch, status, risk, and initial plan.
5. Fill the `/goal` fields or link to `artifacts/plan.md`.
6. During work, append timeline entries for meaningful decisions, checks, blockers, and handoffs.
7. Update checkpoints and trace when the task moves stages.
8. Keep `artifacts/` focused on the currently active task.
9. Before closing the issue, copy final summary, checks, blockers, PR link, and next action into the issue journal.
10. Promote durable project knowledge to `docs/projects/<project>/wiki/` or `docs/projects/<project>/memory/`.

## Why This Exists
- `artifacts/` is the active workbench.
- `docs/projects/<project>/issues/` is the permanent private issue memory.
- Git branches isolate code changes.
- Issue journals make it easy to see what happened on each branch without digging through old artifacts.
- Private execution history stays local in `/Users/user/agents` and is not published to target project repositories by default.
