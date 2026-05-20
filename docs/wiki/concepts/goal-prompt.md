# Goal Prompt

The `/goal` pattern prevents scope creep before implementation.

Every non-trivial issue should define:
- `GOAL`: one measurable result.
- `CONTEXT`: repository, branch, issue, relevant files, dependencies.
- `CONSTRAINTS`: protected paths, risk gates, compatibility rules.
- `PRIORITY`: primary, secondary, and explicitly non-goals.
- `PLAN`: small ordered steps.
- `DONE WHEN`: observable completion state.
- `VERIFY`: checks and expected evidence.
- `OUTPUT`: files, artifacts, issue journal, PR notes.
- `STOP RULES`: conditions that require human approval.

Use `docs/templates/goal.md` when starting a new issue.
