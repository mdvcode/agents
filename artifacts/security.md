# Security

## Summary

- Selected change-set paths are scanned before `git add` through `scripts/security_scan.py --paths-file`.
- Required security scan or required profile security command failure blocks publication.
- Optional security command failures remain warnings according to project policy.
- Publication runs from an isolated worktree, reducing exposure to unrelated staged files or dirty main working-tree changes.

## Project Profile

- Selected profile: `agent_workspace`
- Security command selected: `make security`
- Frontend evidence required: false.

## Findings

- HIGH: none.
- MEDIUM: `scripts/publish_pr.py` can commit, push, and create/update PRs when policy gates pass. It now persists runtime state after irreversible actions and resumes partial failures.
- LOW: `scripts/run_workflow.py` now creates microsecond/random run IDs to avoid same-second collisions.

## Secret Handling

- No secrets were intentionally added.
- Required security failures are hard blockers and are not converted into draft PRs.

## Recommended Action

- `make security` passed in the final verification loop.
