# Agent Memory

Agent memory in this workspace has five layers:

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
- Put recurring mistakes in `docs/memory/lessons_learned.md`.
- Do not publish private project memory to target project GitHub repositories by default.

## Read Policy
- Start with `docs/onboarding.md`.
- Identify the project and read `docs/projects/<project>/privacy.md`.
- Read the relevant project issue journal.
- Read only the project wiki/memory pages related to the task.
- Use `rg` before broad file reads.

## Context and memory boundary

Context Intelligence Platform retrieves static project/Harness knowledge through Context Engine. Memory is a separate layer: `MemoryManager` defines lifecycle operations, but the current milestone does not inject long-term memory into role context and does not implement storage or learning.

The earlier `scripts/project_memory.py` BM25 helper remains a compatibility utility, not an implicit role-context source. A future MemPalace adapter may expose approved memory records as `KnowledgeType.MEMORY` without changing Context Engine, Retriever, Context Builder, or runtime call sites.
