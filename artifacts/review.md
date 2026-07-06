# Review

Findings: none blocking after focused self-review.

Coverage notes:
- Added regression coverage for workflow adapter propagation, default Codex preflight before roles, context budget manifest fields, role artifact ownership, frontend QA unavailable evidence, strict real-Codex smoke expectations, and production executor smoke with fake Codex CLI.
- Existing publication dry-run path still invokes `publish_pr.py` with the same `run_id` and `artifacts_dir`.

Residual risk:
- The real Codex smoke remains opt-in and was skipped locally because `AGENT_REAL_CODEX_SMOKE=1` was not set.
- Browser capability detection is intentionally conservative: absent `AGENT_BROWSER_AVAILABLE=1`, frontend QA records unavailable evidence instead of claiming coverage.
