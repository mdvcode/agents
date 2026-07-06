# Review

Findings:
- Blocking for acceptance: `make codex-smoke` cannot complete locally because the installed Codex CLI package is missing its native vendor binary. The smoke target itself is present and strict.

Implemented:
- Added `make codex-preflight`.
- Added `make codex-smoke`.
- Documented both commands in onboarding and agent-system docs.
- Added tests to keep the Makefile/docs gate present.
- Improved preflight classification when the Codex CLI help probe fails.

Residual risk:
- P3.2 should wait until `make codex-preflight` and `make codex-smoke` pass on a repaired/authenticated Codex CLI installation.
