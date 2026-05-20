# Agent Memory

Agent memory in this workspace has four layers:

1. `artifacts/`: current-task workbench. It can be rewritten or cleaned between issues.
2. `docs/projects/<project>/issues/`: durable private per-issue execution history.
3. `docs/projects/<project>/memory/`: project-private cross-issue memory.
4. `docs/projects/<project>/wiki/`: project-private curated knowledge.
5. `docs/memory/`, `docs/wiki/`, and `docs/graph/`: global agent-system memory only.

## Write Policy
- Put facts about one GitHub issue in `docs/projects/<project>/issues/issue-<number>.md`.
- Put repeated project lessons and cross-issue context in `docs/projects/<project>/memory/topics/`.
- Put stable project knowledge in `docs/projects/<project>/wiki/`.
- Put cross-project agent-system knowledge in global `docs/wiki/` or `docs/memory/`.
- Put recurring mistakes in `artifacts/lessons_learned.md`.
- Do not publish private project memory to target project GitHub repositories by default.

## Read Policy
- Start with `docs/onboarding.md`.
- Identify the project and read `docs/projects/<project>/privacy.md`.
- Read the relevant project issue journal.
- Read only the project wiki/memory pages related to the task.
- Use `rg` before broad file reads.
