# Report

## Summary

- Added `.agent-project-profiles.yaml` with `agent_workspace`, `django`, and `flowfox` profiles.
- Added `artifacts/project_profile.json` and `schemas/project_profile.schema.json`.
- Updated planner, quality runner, test generator, security agent, reviewer, report agent, and orchestrator prompts to use project profiles before selecting checks or publication actions.
- Updated `schemas/quality.schema.json` and `artifacts/quality.json` to require `project_profile` and `commands_attempted`.
- Updated artifact validation and `make check` so `project_profile.json` and `.agent-project-profiles.yaml` are part of the required contract.

## Project profile

- Selected profile: `agent_workspace`
- Reason: task changes the agent control-plane repository, not a Django or Flowfox application repository.
- Quality commands attempted: `make validate-artifacts`, `git diff --check`, `make security`, `make check`
- Security commands attempted: `make security`
- Frontend evidence required: false
- Frontend evidence provided: not applicable

## Checks

- Passed: `make validate-artifacts`
- Passed: `git diff --check`
- Passed: `make security`
- Passed: `make check`

## Risk

- MEDIUM: private process prompts and schemas change future command selection and publication gates across project types.

## Next Action

- Use project profiles before selecting quality checks, security checks, tests, review expectations, or publication gates on future tasks.
