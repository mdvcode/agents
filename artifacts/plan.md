TASK
- P3.1e: add a real Codex smoke gate before P3.2 deterministic routing work.

PROJECT_PROFILE
- Selected profile: agent_workspace.
- Reason: task changes Makefile, local agent docs, runtime preflight behavior, and tests for the agent harness.
- Quality commands: focused pytest for Makefile/preflight/smoke tests; full pytest; `make check`.
- Security commands: `make security`.
- Frontend evidence required: no.

CONTEXT
- P3.1d already added the production Codex executor path, strict real-Codex smoke test, and `scripts/check_codex_runtime.py`.
- The remaining gap from review is making the real Codex smoke an explicit command gate and documenting it before P3.2.

FILES_TO_CHANGE
- `Makefile`
- `docs/onboarding.md`
- `docs/agent-system.md`
- `scripts/check_codex_runtime.py`
- `tests/test_makefile_codex_targets.py`
- `tests/test_codex_runtime_preflight.py`
- Required `artifacts/*` task state files.

RISK
- Initial risk class: medium.
- Rationale: this touches workflow/runtime gates for the harness, but it is small, local, and covered by focused tests.

CHECKS_TO_RUN
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_makefile_codex_targets.py tests/test_codex_runtime_preflight.py tests/test_real_codex_smoke.py -q`
- `make codex-preflight`
- `make codex-smoke`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests`
- `make check`
- `make security`

DONE_CRITERIA
- `make codex-preflight` exists and checks the real Codex runtime.
- `make codex-smoke` exists and runs the strict real-Codex planner smoke.
- Onboarding/system docs tell agents to run both commands before P3.2.
- Preflight reports broken Codex runtime as blocked before role execution.
- If local Codex is not runnable/authenticated, artifacts record the blocker honestly.
