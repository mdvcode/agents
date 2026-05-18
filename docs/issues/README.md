# Issue History

Use this directory for durable execution history per GitHub issue.

## Naming
- Journal file: `docs/issues/issue-<number>.md`
- Branch name: `codex/issue-<number>-<short-name>`
- Kanban card: one short card in `docs/kanban/tasks.md`

Example:
- GitHub issue: `#123`
- Branch: `codex/issue-123-fix-contact-export`
- Journal: `docs/issues/issue-123.md`

## Workflow
1. Create or switch to the issue branch.
2. Copy `docs/issues/_template.md` to `docs/issues/issue-<number>.md`.
3. Fill in issue URL, branch, status, risk, and initial plan.
4. During work, append timeline entries for meaningful decisions, checks, blockers, and handoffs.
5. Keep `artifacts/` focused on the currently active task.
6. Before closing the issue, copy final summary, checks, blockers, PR link, and next action into the issue journal.

## Why This Exists
- `artifacts/` is the active workbench.
- `docs/issues/` is the permanent issue memory.
- Git branches isolate code changes.
- Issue journals make it easy to see what happened on each branch without digging through old artifacts.
