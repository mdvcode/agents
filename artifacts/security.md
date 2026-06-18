# Security

## SUMMARY

- Added a profile-aware security scanner and autonomous publication executor for the private agent harness.
- No application secrets, credentials, auth, billing, production infrastructure, migrations, or deployment paths were changed.

## PROJECT_PROFILE

- Selected profile: `agent_workspace`
- Security commands selected: `make security` via `scripts/security_scan.py`.
- Frontend evidence required: false.

## HIGH

- None.

## MEDIUM

- `scripts/publish_pr.py` can perform commit, push, and PR publication when invoked and when preflight passes. It blocks HIGH risk, protected paths, detected secrets, invalid artifacts, default branches, detached HEAD, merge conflicts, missing git identity, missing remotes, and failed `gh auth`.
- Target repository publication uses `artifacts/change_set.json` and stages only allowlisted files; it never uses `git add -A`.
- Publisher preflight runs selected profile quality commands and downgrades publication to draft when those commands fail without hard blockers.
- Publisher preflight also runs selected profile security commands.
- `CommandRunner` maps missing commands, timeouts, and permission errors to structured results instead of tracebacks.
- `--skip-checks` is blocked outside dry-run test mode.
- Public PR content is sourced from `artifacts/publication_payload.json` instead of internal report artifacts.
- Live publication results are stored in `.agent-runs/<run-id>/` and PR comments rather than tracked artifacts after push/PR creation.

## LOW

- `.agent-policy.yaml` now filters specific forbidden internal-process phrases in public output instead of banning the product term `AI`.

## DJANGO_SECURITY_NOTES

- Not applicable.

## SECRETS

- No secrets added. `make security` passed.

## DEPENDENCY_RISKS

- No new runtime dependencies were added.

## RECOMMENDED_ACTION

- Use `python3 scripts/run_workflow.py publish_pr --dry-run` or `python3 scripts/publish_pr.py --dry-run` before first live publication from a target project branch. Dry-run is read-only and passes in this checkout.
