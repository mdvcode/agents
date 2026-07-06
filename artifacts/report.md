# Report

Task: P3.1d production Codex execution path and role trust boundaries.

Implemented:
- `full_agent_workflow` now declares `adapter_command: "python3 scripts/adapters/codex_cli_executor.py"` and passes it into `agent_role_runner.py`.
- `scripts/run_workflow.py` accepts `--adapter-command` and falls back to workflow-level `adapter_command`.
- Added `scripts/check_codex_runtime.py` for Codex CLI availability, flag, auth/exec, repo, and sandbox preflight.
- Added `scripts/tool_preflight.py` for role-level tool gates, including frontend QA unavailable evidence and publication/quality/security checks.
- `agent_role_runner.py` now defaults the full workflow to the production Codex adapter, blocks production runtime failures before roles, records preflight output, and runs per-role tool preflight.
- `codex_cli_executor.py` enforces role-owned artifact paths before writing returned `artifacts[]`, validates claimed artifacts, preserves raw JSONL, and applies context total/file budgets.
- Context manifests now include `context_budget`, `selected_context`, `excluded_context`, `retrieval_queries`, `source_file_candidates`, and `repo_intelligence`.
- Real Codex smoke is strict when explicitly enabled.

Validation:
- `python3 -m py_compile scripts/run_workflow.py scripts/agent_role_runner.py scripts/adapters/codex_cli_executor.py scripts/check_codex_runtime.py scripts/tool_preflight.py scripts/context_compiler.py` passed.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_run_workflow.py tests/test_agent_role_runner.py tests/test_codex_adapter.py tests/test_context_compiler.py tests/test_full_agent_workflow.py tests/test_real_codex_smoke.py` passed: 28 passed, 1 skipped.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests` passed: 105 passed, 1 skipped.
- `make check` passed.
- `make security` passed.

Warnings:
- Plain `python3 -m pytest ...` failed before test collection due to an unrelated globally installed pytest plugin (`web3/ethpm`) and protobuf incompatibility. The repository Makefile uses `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.
- Real Codex smoke was not run because it requires `AGENT_REAL_CODEX_SMOKE=1` and an authenticated Codex CLI.

Next action:
- Proceed to P3.2 deterministic routing and bounded repair loops after review.
