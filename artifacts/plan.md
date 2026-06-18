# Goal

## GOAL

- Add an executable autonomous publication layer that can validate, security-check, stage an allowlisted change set, commit, push, and create or update a PR when policy gates allow it.

## PROJECT_PROFILE

- Selected profile: `agent_workspace`
- Reason: this task changes the private agent control-plane repository, including schemas, scripts, prompts, workflow config, tests, and audit artifacts.
- Quality commands: `make check`
- Security commands: `make security`
- Frontend evidence required: false
- Matched markers: `AGENTS.md`, `.agents/**`, `schemas/**`, `artifacts/**`, `scripts/**`, `tests/**`, `Makefile`

## CONTEXT

- The harness could previously decide `decision=publish_pr` with `execution_status=planned`, but no executor performed commit, push, or PR creation.
- Security scanning blocked private `artifacts/` and project memory even in the private `agent_workspace`, while those paths must only be blocked for target publication repositories.
- Nested verdict fields were present but their child types and enums were not validated.
- Policy public-output filtering banned the product term `AI` too broadly.

## CONSTRAINTS

- Never use `git add -A`; publication must stage only paths from `artifacts/change_set.json`.
- HIGH risk, secrets, protected paths, invalid artifacts, destructive actions, default branch work, detached HEAD, merge conflicts, missing git identity, missing remote, or failed `gh auth` must block publication.
- Checks failing or visual evidence being unavailable should create or update a draft PR, not a ready PR, when no hard blocker exists.
- Do not auto-merge or deploy.

## RISK

- MEDIUM: this changes private agent harness execution semantics and adds a Git/GitHub executor, but does not touch production systems, credentials, auth, billing, migrations, or deployment.

## PLAN

1. Make `scripts/security_scan.py` profile-aware so private workspace artifacts are allowed for `agent_workspace` but blocked for Flowfox/target publication repositories.
2. Add `artifacts/change_set.json`, `schemas/change_set.schema.json`, `artifacts/publication.json`, and `schemas/publication.schema.json`.
3. Add `scripts/publish_pr.py` with preflight checks, dry-run mode, allowlisted staging, commit, push, existing-PR update, PR creation, partial failure recording, verdict updates, and audit logging.
4. Add `.agent-workflows.yaml` and a GitHub Actions workflow for harness checks.
5. Strengthen artifact validation for nested object types/enums, selected profile commands, publication/change-set artifacts, project workflow config, and policy public-output phrase rules.
6. Update stale artifacts and issue-journal wording.
7. Add focused tests for security scanning, validator contracts, and publication executor behavior.

## DONE WHEN

- `scripts/publish_pr.py` exists and supports `--dry-run`.
- Publication stages only paths listed in `artifacts/change_set.json`.
- `artifacts/publication.json` records branch, commit, push, PR URL/state, warnings, and errors.
- Flowfox target publication blocks private artifacts/memory while agent workspace publication allows private workspace artifacts.
- Nested verdict child types and `pr_state` enum are validated.
- `make check` includes security and all tests.
- `make validate-artifacts`, `make security`, `python3 -m pytest tests`, `make check`, and `git diff HEAD --check` pass.

## VERIFY

- `make validate-artifacts`
- `make security`
- `python3 -m pytest tests`
- `make check`
- `git diff HEAD --check`

## STOP RULES

- Stop before real commit/push/PR if preflight reports a hard blocker.
- Do not publish from a default branch or detached HEAD.
- Do not publish secrets, protected paths, private control-plane files into target project repositories, or HIGH-risk changes.
