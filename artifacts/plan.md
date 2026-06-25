# TASK

Implement the remaining P3.1b production Codex role executor contract items from the attached review.

# PROJECT_PROFILE

Selected profile: `agent_workspace`.

Reason: The work changes the local agent control plane under `/Users/user/agents`: role executor scripts, workflow runner behavior, role contracts, and repository-local tests.

Quality commands:
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_codex_adapter.py tests/test_agent_role_runner.py tests/test_full_agent_workflow.py tests/test_context_compiler.py -q`
- `make check`

Security commands:
- `make security`
- focused review of subprocess usage, artifact path handling, protected paths, and hardcoded secrets

Frontend evidence required: no.

# CONTEXT

The existing P3.1b implementation already has role request fields for `prompt_path`, `output_contract`, `project_profile`, `expected_artifacts`, role capability and contract YAML files, role schemas, context manifests with project profile, high-risk approval gating, and publication dry-run routing tests.

The remaining gaps addressed here:
- `codex_cli_executor.py` accepted `completed` role results without checking that required artifacts actually exist.
- workflow checkpoints did not guarantee `duration_ms` for internal roles and publication.
- publication did not have a role contract for mandatory `publication.json` validation.
- tests did not include a production-executor smoke path through `codex_cli_executor.py` from planner through reviewer.

# FILES_TO_CHANGE

- `.agent-role-contracts.yaml`
- `scripts/adapters/codex_cli_executor.py`
- `scripts/agent_role_runner.py`
- `tests/test_codex_adapter.py`
- `tests/test_full_agent_workflow.py`
- `tests/test_real_codex_smoke.py`
- `artifacts/*`

# DO_NOT_TOUCH

- target project repositories
- secrets, credentials, `.env*`, private keys
- production infrastructure, deployment scripts, billing, payments, auth/session/CSRF surfaces

# INITIAL_RISK_CLASS

Medium. This changes the control-plane workflow executor and publication role contract, but only inside the private agent workspace and without touching protected production or secret-bearing paths.

# DONE_CRITERIA

- Executor loads explicit prompt, context manifest, and role output contract.
- Executor blocks structured `completed` results when expected artifacts are missing or unsafe.
- Role-specific capabilities/contracts remain available to requests and manifests.
- Publication has a mandatory `publication.json` contract.
- Workflow records token usage and duration for every role checkpoint.
- Smoke coverage exercises issue intake through planner, risk, implementation, quality, and reviewer using `codex_cli_executor.py`.
- Focused tests pass.
- `make check` and `make security` pass or blockers are recorded.
