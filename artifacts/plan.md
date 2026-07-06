TASK
- P3.1d: make the full agent workflow use the Codex CLI executor by default and close role trust boundaries.

PROJECT_PROFILE
- Selected profile: agent_workspace.
- Reason: task changes the local agent harness, workflow scripts, schemas, and tests under `/Users/user/agents`.
- Quality commands: `python3 -m pytest ...`; `make check`.
- Security commands: `make security`; focused review for unsafe subprocess/path handling.
- Frontend evidence required: no.

FILES_TO_INSPECT
- `.agent-workflows.yaml`
- `.agent-role-contracts.yaml`
- `.agent-role-capabilities.yaml`
- `scripts/run_workflow.py`
- `scripts/agent_role_runner.py`
- `scripts/adapters/codex_cli_executor.py`
- `scripts/context_compiler.py`
- `schemas/context_manifest.schema.json`
- `tests/test_*workflow*.py`
- `tests/test_agent_role_runner.py`
- `tests/test_codex_adapter.py`
- `tests/test_context_compiler.py`
- `tests/test_real_codex_smoke.py`

FILES_TO_CHANGE
- `.agent-workflows.yaml`
- `scripts/run_workflow.py`
- `scripts/agent_role_runner.py`
- `scripts/adapters/codex_cli_executor.py`
- `scripts/check_codex_runtime.py`
- `scripts/tool_preflight.py`
- `scripts/context_compiler.py`
- `schemas/context_manifest.schema.json`
- Focused tests for workflow adapter defaults, runtime/tool preflight, artifact ownership, context budget, and real Codex smoke.
- Required `artifacts/*` state files for this task.

DO_NOT_TOUCH
- Target project private issue journals and Flowfox artifacts except existing dirty files already present.
- `.env*`, secrets, credentials, production infra, migrations, auth, billing, payments.
- Existing unrelated user changes.

RISK
- Initial risk class: medium.
- Rationale: workflow execution and trust boundaries are core harness behavior, but changes are local, reviewable, and avoid protected production paths.

CHECKS_TO_RUN
- `python3 -m pytest tests/test_run_workflow.py tests/test_agent_role_runner.py tests/test_codex_adapter.py tests/test_context_compiler.py tests/test_real_codex_smoke.py`
- `python3 -m pytest tests`
- `make check`
- `make security`

DONE_CRITERIA
- `full_agent_workflow` passes `scripts/adapters/codex_cli_executor.py` by default.
- `scripts/run_workflow.py` accepts `--adapter-command`.
- `scripts/check_codex_runtime.py` exists and blocks before role execution when the production Codex path is unavailable.
- Roles can only write contract-owned artifacts through harness-managed `artifacts[]`.
- Role-specific tool preflight reports blockers or unavailable evidence before pretending a role ran.
- Context manifests include global budget, selected/excluded context, retrieval queries, source candidates, and repo intelligence.
- Real Codex smoke is strict when explicitly enabled.
- Publication still invokes `publish_pr.py` with the same `run_id` and `artifacts_dir`.
