# TASK

P3.1: add a real Codex adapter path and unified run context so the full agent workflow stops producing fake successful checkpoints and instead uses run-scoped role requests, context manifests, strict role results, task worktrees, artifact validation, and the safe publication executor.

# PROJECT_PROFILE

Selected profile: `agent_workspace`.

Reason: The work is in `/Users/user/agents` and changes the local agent control-plane harness: scripts, schemas, workflow config, tests, and root task artifacts.

Quality commands:
- `make check`
- focused pytest for workflow/adapter/validator tests

Security commands:
- `make security`

Frontend evidence required: no.

# CONTEXT

`scripts/agent_role_runner.py` currently returns deterministic successful checkpoints when no LLM command is configured. `scripts/run_workflow.py` creates a run directory but does not pass one stable run context through task/repository/worktree/publication execution. `scripts/validate_artifacts.py` validates only root `artifacts/`. Publication already supports `--artifacts-dir` and `--run-id`, so P3.1 should wire role publication through that existing safe executor.

# FILES_TO_INSPECT

- `scripts/agent_role_runner.py`
- `scripts/run_workflow.py`
- `scripts/validate_artifacts.py`
- `scripts/worktree_manager.py`
- `scripts/repository_registry.py`
- `scripts/publish_pr.py`
- `.agent-workflows.yaml`
- existing tests under `tests/`

# FILES_TO_CHANGE

- `scripts/adapters/codex_adapter.py`
- `scripts/context_compiler.py`
- `scripts/agent_role_runner.py`
- `scripts/run_workflow.py`
- `scripts/validate_artifacts.py`
- `scripts/worktree_manager.py`
- `.agent-workflows.yaml`
- `schemas/context_manifest.schema.json`
- `schemas/role_request.schema.json`
- `schemas/role_result.schema.json`
- `tests/test_codex_adapter.py`
- `tests/test_full_agent_workflow.py`
- existing focused tests as needed
- `scripts/security_scan.py`
- `.agent-repositories.yaml`
- `.agents/skills/*/SKILL.md`

# DO_NOT_TOUCH

- target project private memory except required root task artifacts
- Flowfox repository files
- migrations, auth, billing, payments, secrets, credentials, production infrastructure
- unrelated dirty Flowfox screenshots and issue journals currently present in the workspace

# ASSUMPTIONS

- The real runtime is configured through an external command environment variable, while tests can use a fake command.
- Without that adapter command, the workflow must return `blocked`.
- The publication role should call `scripts/publish_pr.py` with the same `run_id` and run-scoped artifacts directory.
- Existing prompt-only specialist roles can still run through the same adapter contract; deeper specialist implementations remain later work.

# CHECKS_TO_RUN

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_codex_adapter.py tests/test_agent_role_runner.py tests/test_run_workflow.py tests/test_validate_artifacts.py tests/test_full_agent_workflow.py`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_context_compiler.py tests/test_security_scan.py tests/test_real_codex_smoke.py`
- `make check`
- `make security`

# INITIAL_RISK_CLASS

Medium. This changes orchestration behavior and publication routing in the agent control plane, but does not touch protected production/auth/billing/secret paths.

# DONE_CRITERIA

- Missing adapter configuration blocks the workflow instead of pretending success.
- One `run_id` flows through workflow runner, role runner, context manifests, artifacts, worktree metadata, and publication command.
- Planner, Risk, and Implementation are executed through `CodexAdapter`.
- Role requests and results are schema-validated.
- `validate_artifacts.py` accepts `--artifacts-dir`.
- `publication` invokes `scripts/publish_pr.py` with run-scoped artifacts and same `run_id`.
- HIGH risk produces `awaiting_approval` and does not publish.
- Integration test covers a fake adapter full workflow.
- Optional real Codex smoke path is documented/available without making local checks depend on it.
- Planner creates a non-empty run-scoped `plan.md`, or the workflow blocks.
- Risk Classifier creates schema-valid run-scoped `risk.json`, or the workflow blocks.
- Implementation cannot mutate the source repository when a task worktree is active.
- Missing CI base/head refs fail closed in `security_scan.py`.
- Skills have YAML frontmatter and context manifests reference only role-relevant skills.
