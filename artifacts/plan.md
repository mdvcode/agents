# Goal

## GOAL

- Harden the autonomous publication executor so live use is safer: isolated staging, strict policy gates, portable target repositories, public-safe PR payloads, command error handling, and end-to-end Git/PR tests.
- Resolve agent_workspace runtime-state circularity by keeping live publication results out of tracked artifacts and writing them to `.agent-runs/<run-id>/` plus PR comments.
- Add a minimal executable workflow runner with bounded iterations, retry/backoff, stop conditions, and run-level traces.

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
8. Run required profile quality commands during publisher preflight.
9. Keep `--dry-run` read-only for publication artifacts, verdict, report, and audit log.
10. Record live publication results in `artifacts/report.md` and Flowfox issue journals when an issue journal exists.
11. Block unrelated pre-staged files, stale change-set paths, unsafe paths, git-add failures, and staged-set mismatches.
12. Use `artifacts/publication_payload.json` for public PR title/body/commit message.
13. Verify actual PR draft/ready state after create/update.
14. Replace publication result sections by marker instead of appending duplicates.
15. Move live runtime publication state to `.agent-runs/<run-id>/` and post the result as a PR comment instead of mutating tracked artifacts after publication.
16. Add `scripts/run_workflow.py` for bounded workflow execution and trace capture.

## DONE WHEN

- `scripts/publish_pr.py` exists and supports `--dry-run`.
- Publication stages only paths listed in `artifacts/change_set.json`.
- `artifacts/publication.json` records branch, commit, push, PR URL/state, warnings, and errors.
- Publisher preflight runs the required quality commands from the selected project profile.
- Publisher preflight runs the required security commands from the selected project profile.
- Publication blocks unrelated staged files, stale/missing include paths, unsafe absolute/parent paths, git-add failures, and staged-set mismatches.
- Public PR title/body/commit message come from `artifacts/publication_payload.json`.
- Publication result sections are marker-replaced and idempotent.
- Live publication writes runtime state to `.agent-runs/<run-id>/` and does not mutate tracked artifacts after commit/push/PR.
- Workflow runner writes `.agent-runs/<run-id>/workflow_trace.jsonl`.
- `--dry-run` does not mutate publication artifacts, verdict, report, audit log, git index, commits, pushes, or PRs.
- Live publication appends a publication summary to `artifacts/report.md` and the matching Flowfox issue journal when applicable.
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
