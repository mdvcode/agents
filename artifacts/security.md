# Security

## Summary

- Selected change-set paths are scanned before `git add` through `scripts/security_scan.py --paths-file`.
- CI can now scan changed files directly through `scripts/security_scan.py --base-ref --head-ref`, avoiding empty staged-file scans in GitHub Actions checkouts.
- Trusted repository metadata now lives in `.agent-repositories.yaml` and is validated by artifact checks.
- `publish_pr.py` verifies the target remote/profile/base branch against the trusted registry when present.
- Change-set completeness is checked before publication so real changed files are not silently omitted from the selected set.
- Required security scan or required profile security command failure blocks publication.
- Optional security command failures remain warnings according to project policy.
- Publication runs from an isolated worktree, and `scripts/worktree_manager.py` can bootstrap a task worktree at workflow start.
- Dry-run now also creates a disposable worktree and runs selected-file security plus required profile checks, so selected secrets are caught before live publication.
- Publication branches are restricted to task-style prefixes and protected branches such as `main`, `master`, `trunk`, and release-style prefixes are blocked.
- Allowed branch prefixes are read from policy/registry and validated as `feat/`, `fix/`, `issue/`, and literal `tast/`.

## Project Profile

- Selected profile: `agent_workspace`
- Security command selected: `make security`
- Frontend evidence required: false.

## Findings

- HIGH: none.
- MEDIUM: `scripts/publish_pr.py` can commit, push, and create/update PRs when policy gates pass. It now also requires trusted registry alignment when configured and blocks incomplete selected change sets.
- LOW: `scripts/run_workflow.py` and `scripts/agent_role_runner.py` write run-scoped traces/artifacts under `.agent-runs/`.

## Secret Handling

- No secrets were intentionally added.
- Required security failures are hard blockers and are not converted into draft PRs.

## Recommended Action

- `make security` passed in the final verification loop.
