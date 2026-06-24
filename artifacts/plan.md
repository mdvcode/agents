# TASK

P3.1b: add a production Codex role executor layer with explicit role prompts, role-specific capabilities, output contracts, project profile propagation, and artifact completion gates.

# PROJECT_PROFILE

Selected profile: `agent_workspace`.

Reason: The work is in `/Users/user/agents` and changes the local agent control-plane harness: scripts, schemas, workflow config, tests, and root task artifacts.

Quality commands:
- `make check`
- focused pytest for adapter, role runner, context compiler, and full workflow tests

Security commands:
- `make security`

Frontend evidence required: no.

# CONTEXT

The previous P3.1 work introduced run-scoped role requests, context manifests, strict role results, task worktrees, and safe publication routing. The attached review says the next gap is P3.1b: the workflow still needs explicit role prompt paths, per-role tool/sandbox capability metadata, role-specific output contract paths, expected artifacts, and stricter completion gates for all critical roles.

# FILES_TO_INSPECT

- `scripts/adapters/codex_adapter.py`
- `scripts/agent_role_runner.py`
- `scripts/context_compiler.py`
- `schemas/role_request.schema.json`
- `schemas/role_result.schema.json`
- `schemas/context_manifest.schema.json`
- `.agent-workflows.yaml`
- existing tests under `tests/`

# FILES_TO_CHANGE

- `scripts/adapters/codex_adapter.py`
- `scripts/adapters/codex_cli_executor.py`
- `scripts/agent_role_runner.py`
- `scripts/context_compiler.py`
- `.agent-role-capabilities.yaml`
- `.agent-role-contracts.yaml`
- `schemas/role_request.schema.json`
- `schemas/context_manifest.schema.json`
- `schemas/roles/*.schema.json`
- focused tests under `tests/`
- current task artifacts

# DO_NOT_TOUCH

- target project private memory
- Flowfox repository files
- migrations, auth, billing, payments, secrets, credentials, production infrastructure
- unrelated dirty screenshots and issue journals currently present in the workspace

# ASSUMPTIONS

- `CodexAdapter` remains the strict shell boundary for role execution.
- `scripts/adapters/codex_cli_executor.py` is the production executor command that can be selected through `AGENT_CODEX_COMMAND`.
- Local tests can use fake adapter commands and should not require a real Codex CLI.
- Role-specific capabilities are declared and propagated now; OS-level sandbox enforcement can still be delegated to the concrete executor/runtime.

# CHECKS_TO_RUN

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_codex_adapter.py tests/test_agent_role_runner.py tests/test_context_compiler.py tests/test_full_agent_workflow.py`
- `make check`
- `make security`

# INITIAL_RISK_CLASS

Medium. This changes orchestration and execution contracts in the agent control plane, but does not touch protected production/auth/billing/secret paths.

# DONE_CRITERIA

- RoleRequest includes `prompt_path`, `output_contract`, `project_profile`, `expected_artifacts`, and capability/sandbox metadata.
- Context manifests include non-empty project profile and role-specific capabilities.
- Role contracts and capabilities live in reviewable YAML files.
- Critical roles cannot return `completed` without required artifacts.
- JSON artifacts are validated against role-specific schemas where available.
- `codex_cli_executor.py` reads the role prompt, context manifest, and output contract and returns structured role failures instead of tracebacks.
- Focused tests cover role request enrichment, capability propagation, artifact gates, and executor behavior.
- `make check` and `make security` pass or blockers are recorded.
