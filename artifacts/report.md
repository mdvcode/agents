# Project Profile

Selected profile: `agent_workspace`

Reason: This task changes the local agent workflow harness, schemas, tests, and root task artifacts.

# Changes

- Added `.agent-role-capabilities.yaml` for per-role tools and filesystem access metadata.
- Added `.agent-role-contracts.yaml` for prompt paths, role output contract paths, expected artifacts, and artifact schemas.
- Added `scripts/adapters/codex_cli_executor.py` as a production stdin/stdout executor wrapper for a configured Codex CLI command.
- Extended `RoleRequest` and context manifests with prompt, contract, project profile, expected artifact, tool, and filesystem metadata.
- Added role-specific schemas under `schemas/roles/`.
- Reworked role artifact validation to enforce declared completion gates across critical roles.
- Updated adapter/runner/context compiler tests for the richer contracts and executor behavior.

# Checks

- Pass: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_codex_adapter.py tests/test_agent_role_runner.py tests/test_context_compiler.py tests/test_full_agent_workflow.py` (14 passed)
- Pass: `make check` (artifact validation passed; security scan passed; JSON artifacts valid; 93 pytest tests passed; 1 optional real Codex smoke test skipped)
- Pass: `make security` (no obvious secrets, private keys, private paths, or protected staged files found)

# Risk

Medium. The change modifies orchestration and execution contracts but does not touch protected production/auth/billing/secret paths.

# Blockers

None currently known.

# Next Action

Review the P3.1b patch. The next implementation slice after this is P3.2 dynamic routing and bounded repair loops.
