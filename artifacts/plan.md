TASK
- P4.1: RAG-powered project memory retrieval.

PROJECT_PROFILE
- Selected profile: agent_workspace.
- Reason: the task changes the local context compiler, memory retrieval, tests, documentation, and current-task artifacts.
- Quality commands: focused context compiler/retrieval pytest; full pytest; `make validate-artifacts`; `make check`.
- Security commands: `make security`.
- Frontend evidence required: no.

CONTEXT
- Project memory is stored as private Markdown under `docs/projects/<project>/` and global agent-system memory under `docs/memory/`, `docs/wiki/`, and `docs/graph/`.
- Context manifests already expose `selected_context`, `retrieval_queries`, `source_file_candidates`, and `repo_intelligence`, but the compiler leaves them empty.
- The Codex executor already injects `context_files` into role prompts under a total and per-file byte budget.

FILES_TO_INSPECT
- `scripts/context_compiler.py`
- `scripts/adapters/codex_cli_executor.py`
- `schemas/context_manifest.schema.json`
- `tests/test_context_compiler.py`
- `docs/wiki/concepts/agent-memory.md`
- `docs/graph/files.md`
- `docs/graph/workflows.md`

FILES_TO_CHANGE
- `scripts/project_memory.py`
- `scripts/context_compiler.py`
- `tests/test_project_memory.py`
- `tests/test_context_compiler.py`
- `docs/wiki/concepts/agent-memory.md`
- `docs/wiki/index.md`
- `docs/graph/files.md`
- `docs/graph/workflows.md`
- `docs/kanban/tasks.md`
- current P4.1 artifacts and audit log

DO_NOT_TOUCH
- Do not use external embedding APIs or send private memory over the network.
- Do not retrieve files outside approved private memory roots.
- Do not change publication, auth, billing, migration, secret-management, or deployment behavior.

ASSUMPTIONS
- Deterministic local BM25 retrieval qualifies as the retrieval stage of RAG; the existing role model remains the generation stage.
- Markdown heading sections are the appropriate retrieval chunks for the current memory layout.
- `agent_workspace` uses global agent-system memory; registered target projects use only their private project memory roots.

CHECKS_TO_RUN
- `python3 -m py_compile scripts/project_memory.py scripts/context_compiler.py`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_project_memory.py tests/test_context_compiler.py tests/test_codex_adapter.py -q`
- `make validate-artifacts`
- `make security`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests`
- `make check`

INITIAL_RISK_CLASS
- Medium.
- Rationale: private memory selection changes what context is disclosed to role executions, so scope and path controls are security-relevant. It does not change protected paths or publication reachability.

DONE_CRITERIA
- Relevant project-memory sections are ranked locally and deterministically for the task goal and role.
- Retrieval is limited to approved project/global memory roots, regular Markdown files, and configured byte/result budgets.
- Selected chunks and provenance appear in the context manifest and a generated retrieval context file is injected into the role prompt.
- Empty or irrelevant memory degrades safely to no retrieved context.
- Focused tests, full tests, artifact validation, security checks, and diff hygiene pass.
