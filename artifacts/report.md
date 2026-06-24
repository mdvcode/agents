# Project Profile

Selected profile: `agent_workspace`

Reason: This task changes the local agent workflow harness, schemas, tests, and root task artifacts.

# Changes

- Added `scripts/adapters/codex_adapter.py` for strict external role execution via `AGENT_CODEX_COMMAND` / `AGENT_LLM_COMMAND`.
- Added `scripts/context_compiler.py` and context manifest/request/result schemas.
- Reworked `scripts/agent_role_runner.py` to use one run id, run-scoped artifacts, per-role context manifests, strict role result validation, task worktree setup, high-risk approval stop, and `publish_pr.py` for publication.
- Updated `scripts/run_workflow.py`, `scripts/validate_artifacts.py`, `scripts/worktree_manager.py`, `scripts/publish_pr.py`, and `.agent-workflows.yaml` for unified run context and run-scoped validation.
- Added focused and integration tests for adapter behavior, blocked missing-adapter behavior, custom artifacts dir validation, and worktree isolation.
- Added runtime artifact enforcement for Planner `plan.md`, Risk `risk.json`, and Implementation source-repository isolation.
- Added HIGH-risk approval gate regression coverage, real `publish_pr.py` invocation coverage, optional real Codex smoke coverage, CI diff fail-closed behavior, and role-scoped skill references.
- Added YAML frontmatter to local `.agents/skills/*/SKILL.md` files.

# Checks

- Pass: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_codex_adapter.py tests/test_agent_role_runner.py tests/test_run_workflow.py tests/test_validate_artifacts.py tests/test_full_agent_workflow.py` (30 passed)
- Pass: `make check` (artifact validation passed; security scan passed; 91 pytest tests passed; 1 optional real Codex smoke test skipped; diff whitespace check passed)
- Pass: `make security` (no obvious secrets, private keys, private paths, or protected staged files found)

# Risk

Medium. The change modifies orchestration and publication routing behavior but does not touch protected production/auth/billing/secret paths.

# Blockers

None currently known.

# Next Action

Review the P3.1 patch. Publication should use `publish_pr.py` only after excluding unrelated dirty Flowfox artifacts and journals from the staged set.
