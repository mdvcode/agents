# Token Hygiene

The PDF highlights wasted context as a major cost for coding agents. This workspace reduces waste by using layered memory and targeted reads.

## Rules
- Read indexes first: `docs/index.md`, `docs/wiki/index.md`, issue journal, kanban.
- Use `rg` to find exact files and terms.
- Avoid pasting long logs into prompts.
- Keep `artifacts/` current-task only.
- Summarize durable knowledge into `docs/wiki/` and `docs/memory/`.
- Prefer report-only analysis for broad scans unless the user asks to patch.

## Anti-Patterns
- Reading the whole repository before knowing the target.
- Leaving stale sweep outputs in `artifacts/`.
- Repeating known lessons instead of updating memory.
- Using one long issue journal as a substitute for wiki pages.
