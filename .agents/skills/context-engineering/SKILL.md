# Context Engineering Skill

## Purpose
Gather enough context to act without wasting tokens.

## Workflow
1. Identify the target project and read its `privacy.md`.
2. Read indexes first: `docs/index.md`, global `docs/wiki/index.md`, project wiki/memory, relevant project issue journal, and kanban.
3. Use `rg` for exact symbols, file names, errors, and issue terms.
4. Read only targeted files and neighboring code needed to match local patterns.
5. Summarize findings into `artifacts/plan.md` or the project issue journal.
6. Promote durable project findings to `docs/projects/<project>/wiki/` or `docs/projects/<project>/memory/topics/` after the task.

## Rules
- Do not read broad directories unless the plan is insufficient.
- Do not paste large logs into artifacts.
- Prefer curated project wiki pages before raw historical artifacts.
