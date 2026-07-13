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
- Put recurring mistakes in `artifacts/lessons_learned.md`.
- Do not publish private project memory to target project GitHub repositories by default.

## Read Policy
- Start with `docs/onboarding.md`.
- Identify the project and read `docs/projects/<project>/privacy.md`.
- Read the relevant project issue journal.
- Read only the project wiki/memory pages related to the task.
- Use `rg` before broad file reads.

## RAG Retrieval
The context compiler augments each role prompt with relevant private project memory before generation:

1. Build a query from the task goal and current role.
2. Scope candidates to Markdown under the active project's `memory/`, `wiki/`, `graph/`, and `issues/` directories. Target-project retrieval is disabled when `privacy.md` is absent.
3. Split documents at Markdown headings and rank sections with deterministic local BM25.
4. Select at most six sections within a 22 KB retrieval budget.
5. Write a run-local retrieval file with source paths, headings, and scores, then reference it from the role context manifest.

For the `agent_workspace` profile, retrieval uses only global agent-system `docs/memory/`, `docs/wiki/`, and `docs/graph/`. It never mixes target-project memory into the global profile or one target project's memory into another.

Retrieval is local and makes no embedding/API calls. Retrieved memory is private, potentially stale supporting context; `AGENTS.md`, `.agent-policy.yaml`, project privacy policy, and current repository evidence remain authoritative.
