# Report

## Summary

- Added `--paths-file` to `scripts/security_scan.py` so selected change-set files are scanned before staging.
- Reworked `scripts/publish_pr.py` around isolated task worktrees under `.agent-worktrees/` and runtime state under `.agent-runs/<run-id>/publication.json`.
- Added resume behavior for commit-created/push-failed and push-created/PR-failed runs; completed runs return no-op instead of creating duplicate commits or PRs.
- Used `base_branch` from `artifacts/publication_payload.json` for PR create/update.
- Separated required security failures from quality failures: required security blocks publication, while quality failures keep publication as draft.
- Removed live CLI `--skip-checks`; the internal bypass remains limited to dry-run unit tests with `AGENT_HARNESS_TEST_MODE=1`.
- Added regression coverage for selected unstaged secrets, required security failures, quality draft PRs, base branch handling, resume/idempotency, malformed artifacts, malformed `gh` JSON, unique run IDs, PR comment warnings, and unrelated main working-tree changes.
- Closed review blockers: direct `main` publication is blocked, completed no-op now depends on an input fingerprint, pre-commit blocked runs can retry after fixes, irreversible resume respects the current verdict, dry-run executes selected scan/checks, base branch must exist at `origin/<base_branch>`, and optional profile commands are not auto-run.

## Project Profile

- Selected profile: `agent_workspace`
- Reason: task changes private harness scripts, schemas, tests, and artifacts.
- Frontend evidence required: false.

## Checks

- Passed: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests` (69 tests)
- Passed: `make validate-artifacts`
- Passed: `make security`
- Passed: `make check`
- Passed: `git diff HEAD --check`

## Risk

- MEDIUM: publication automation semantics changed, but auto-merge/deploy remain disabled and no protected production/auth/billing/secret paths were touched.

## Next Action

- Review the diff and publish only if the policy/verdict gates remain satisfied.
