# TASK

P3.1c: close the production Codex execution path so role execution uses `codex exec`, standard output schemas, sandbox mapping, harness-owned non-code artifacts, telemetry capture, deterministic publication inputs, and longer workflow orchestration timeout.

# PROJECT_PROFILE

Selected profile: `agent_workspace`.

Reason: The task changes the private agent control-plane repository: executor scripts, workflow runner behavior, context compiler, role contracts, schemas, tests, and artifacts.

Quality commands:
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_codex_adapter.py tests/test_agent_role_runner.py tests/test_full_agent_workflow.py tests/test_context_compiler.py tests/test_run_workflow.py -q`
- `make check`

Security commands:
- `make security`
- focused subprocess/path review

Frontend evidence required: no.

# CONTEXT

The attached review says P3.1b had strong contracts and fake-executor tests, but real Codex execution was not closed. P3.1c must make `codex_cli_executor.py` use `codex exec`, pass an explicit sandbox and output schema, write non-code artifacts from the returned JSON instead of asking read-only roles to modify files, preserve read-only repository snapshots, capture raw JSONL/usage telemetry, prepare publisher inputs deterministically, and avoid a 300-second outer timeout for the full workflow.

# FILES_TO_CHANGE

- `.agent-role-capabilities.yaml`
- `.agent-role-contracts.yaml`
- `.agent-workflows.yaml`
- `schemas/role_result.schema.json`
- `schemas/standard_role_result.schema.json`
- `scripts/adapters/codex_adapter.py`
- `scripts/adapters/codex_cli_executor.py`
- `scripts/agent_role_runner.py`
- `scripts/context_compiler.py`
- `scripts/run_workflow.py`
- `tests/test_agent_role_runner.py`
- `tests/test_codex_adapter.py`
- `tests/test_context_compiler.py`
- `tests/test_full_agent_workflow.py`
- `tests/test_real_codex_smoke.py`
- `tests/test_run_workflow.py`
- `artifacts/*`

# DO_NOT_TOUCH

- target project repositories
- secrets, `.env*`, private keys, credentials, auth, billing, payments, migrations, production infrastructure, deployment scripts

# INITIAL_RISK_CLASS

Medium. This changes the agent control-plane executor and workflow gates, but it stays inside the private agent workspace and does not touch production/protected target-project surfaces.

# DONE_CRITERIA

- Executor invokes `codex exec`, not interactive `codex`.
- Executor passes `--json`, explicit `--sandbox`, `--ask-for-approval never`, `--output-schema`, and `--output-last-message`.
- Harness writes returned non-code artifacts from `artifacts[]`.
- Read-only roles are blocked if repository snapshots change.
- Write roles remain constrained to task worktree publication flow.
- Publication inputs are owned by deterministic `publication-prepare`.
- Context contents are included in the sandboxed prompt.
- Codex JSONL raw stream and usage telemetry are captured.
- Workflow supports longer full-chain orchestration timeout.
- Focused and full checks pass or blockers are recorded.
