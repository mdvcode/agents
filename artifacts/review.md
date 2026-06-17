# Review

## SUMMARY

- Process-only update adding project profiles for the agent workspace, Django, and Flowfox so checks and publication gates are selected by repository/task type.

## CORRECTNESS_FINDINGS

- No code correctness findings; no application code changed.

## DJANGO_DRF_FINDINGS

- Not applicable.

## ARCHITECTURE_FINDINGS

- The new policy and rules commit every task-scoped changed/added/deleted public Flowfox file, use configured git identity plus the authenticated GitHub account, keep private control-plane files out of Flowfox commits and PRs, and require public branch/text metadata to omit agent/Codex/AI/automation wording.
- Project profile guidance preserves the Flowfox publication rules while preventing Django-centric checks from becoming the default for Flowfox tasks.
- `quality.json` and `project_profile.json` now use structured fields that the validator checks.

## PROJECT_PROFILE_FINDINGS

- Selected profile `agent_workspace` is correct for changes under `/Users/user/agents`.
- Selected quality commands match `.agent-project-profiles.yaml`: `make validate-artifacts` and `make check`.
- No Django/Python or Flowfox/Bun commands are used as the main quality signal for this agent workspace task.

## POLICY_VIOLATIONS

- None found.

## KNOWN_LESSON_CONFLICTS

- None found.

## SUGGESTED_PATCH

- No further patch required.

## NOTES

- Publishing remains gated by `.agent-policy.yaml` and now also by the presence and consistency of `artifacts/project_profile.json`.
