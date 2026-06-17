# Report

## Summary

- Added `.agent-policy.yaml` as the machine-readable source of truth for autonomy, publication, protected paths, and human approval gates.
- Replaced the remaining Flowfox approve-gated prompt/skill paths with automated commit, push, and PR creation for completed LOW/MEDIUM issue work after verification and required local evidence.
- Added the explicit `Autonomy and publication` rule in `AGENTS.md` and the explicit `Flowfox publication policy` block in the orchestrator prompt.
- Required every task-scoped changed/added/deleted public Flowfox file to be included in the commit, using configured `user.name` / `user.email` and the authenticated GitHub account.
- Preserved private-file exclusions, public-safe branch names and sanitized public wording with no agent/Codex/AI/automation mentions, HIGH/protected stop rules, no auto-merge, and no deploy.
- Strengthened `risk.json` and `verdict.json` schemas, and taught artifact validation to enforce simple types, required nested fields, and `.agent-policy.yaml` presence.

## Checks

- Passed: `make validate-artifacts`
- Passed: `git diff --check`
- Passed: `make security`
- Passed: `make check`

## Risk

- MEDIUM: private process documentation and schemas change future Flowfox publication behavior by removing the separate approval step for completed LOW/MEDIUM issue work.

## Next Action

- Use this workflow on the next completed LOW/MEDIUM Flowfox issue: implement, verify, capture local visual evidence when required, commit task-scoped public files, push, create/update the PR, and send both PR and local site URLs.
