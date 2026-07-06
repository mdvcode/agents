# Report

Task: P3.1e real Codex smoke gate.

Implemented:
- Added `codex-preflight` and `codex-smoke` targets to `Makefile`.
- `codex-preflight` runs `scripts/check_codex_runtime.py --repo .`.
- `codex-smoke` runs the strict real-Codex planner smoke with `AGENT_REAL_CODEX_SMOKE=1`, `AGENT_CODEX_CLI_COMMAND=codex`, and pytest plugin autoload disabled.
- The Makefile now prefers an available Node 22 or Node 20 path for Codex targets, avoiding the old system Node v8.9.4 ESM failure.
- Documented both commands in `docs/onboarding.md` and `docs/agent-system.md`.
- Improved `scripts/check_codex_runtime.py` so a failed Codex help probe blocks as a runtime/auth failure.
- Added regression tests for Makefile/docs target presence and failed help-probe classification.

Validation:
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_makefile_codex_targets.py tests/test_real_codex_smoke.py -q` passed: 2 passed, 1 skipped.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_makefile_codex_targets.py tests/test_codex_runtime_preflight.py -q` passed: 3 passed.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_makefile_codex_targets.py tests/test_codex_runtime_preflight.py tests/test_real_codex_smoke.py -q` passed: 3 passed, 1 skipped.
- `make validate-artifacts` passed.
- `git diff --check` passed.
- `make check` passed: 108 passed, 1 skipped.
- `make security` passed.
- `make codex-preflight` failed as intended for a broken local Codex runtime.
- `make codex-smoke` failed as intended for a broken local Codex runtime.

Runtime blocker:
- With the original PATH, `codex` uses `/usr/local/bin/node v8.9.4` and fails on ESM `import`.
- With Node 22 first in PATH, `codex` fails because the installed package is missing the native binary at `node_modules/@openai/codex-darwin-x64/vendor/x86_64-apple-darwin/codex/codex`.

Next action:
- Repair/reinstall the local Codex CLI, then run `make codex-preflight` and `make codex-smoke`. Do not start P3.2 until both pass.
