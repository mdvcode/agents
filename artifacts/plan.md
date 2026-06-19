# Goal

## GOAL

- Harden `scripts/publish_pr.py` so LOW/MEDIUM publication is safe, resumable, and idempotent.
- Enforce preflight policy gates before staging, scan selected files before `git add`, publish from an isolated worktree, persist runtime state after irreversible steps, and resume partial commit/push/PR failures without duplicate commits or PRs.

## PROJECT_PROFILE

- Selected profile: `agent_workspace`
- Reason: this task changes the private agent harness repository under `/Users/user/agents`.
- Quality commands: `make check`
- Security commands: `make security`
- Frontend evidence required: false

## CONTEXT

- The prior executor had allowlisted staging and PR creation, but it operated directly in the target working tree and did not have a full runtime state machine.
- Security scanning depended on staged files unless full-repo mode was used.
- Partial failures after commit or push could not reliably resume without creating new publication state.

## CONSTRAINTS

- Do not auto-merge or deploy.
- HIGH risk, policy-denied autonomy, verdict blockers, protected paths, required security findings, invalid artifacts, missing target repositories, malformed config, missing commands, and malformed `gh` JSON must produce structured blocked/failed results.
- Quality failures and missing required visual evidence produce a draft PR, not a ready PR.
- Required security failures block publication and cannot be downgraded to warnings.
- `--skip-checks` must not be available as a live CLI bypass.

## RISK

- MEDIUM: changes the autonomous publication harness and Git/GitHub execution semantics, but does not touch auth, billing, migrations, production infrastructure, deployment, or secrets.

## PLAN

1. Add selected-path scanning to `scripts/security_scan.py` via `--paths-file`.
2. Replace direct publication flow with runtime state: `planned`, `preflight_passed`, `staged`, `committed`, `pushed`, `pr_published`, `completed`, `blocked`, `failed`.
3. Create isolated `.agent-worktrees/<task-id>-<suffix>` worktrees and copy only selected change-set paths into them before scan/stage/commit/push/PR.
4. Persist `.agent-runs/<run-id>/publication.json` after preflight, commit, push, PR publication, and finalization.
5. Resume from matching runtime state by task, target repository, and branch.
6. Use `base_branch` from `artifacts/publication_payload.json` for PR create/update.
7. Finalize runtime summary, markdown, tracked publication/verdict/report, and audit log from one final result.
8. Remove live CLI `--skip-checks`; keep the internal test bypass gated by `AGENT_HARNESS_TEST_MODE=1` and dry-run.
9. Add regression and end-to-end tests for selected secret scanning, required security blocking, quality draft PR, base branch, resume, idempotency, malformed artifacts/gh JSON, unique run IDs, comment warnings, and unrelated working-tree changes.

## DONE WHEN

- Selected unstaged files are scanned before staging.
- Required security failure blocks publication.
- Quality failure creates a draft PR.
- Publication runs in an isolated worktree.
- Unrelated main working-tree changes are not committed.
- `base_branch` is passed to `gh pr create` and corrected on update.
- Resume does not create a new commit or second PR.
- Missing/malformed artifacts return structured results without tracebacks.
- Runtime artifacts and tracked summary fields agree after finalization.
- `make check` and the full pytest suite pass.

## VERIFY

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests`
- `make validate-artifacts`
- `make security`
- `make check`
- `git diff HEAD --check`
