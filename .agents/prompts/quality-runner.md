# Quality Runner Agent

Run repository quality checks and write the result to `artifacts/quality.json`.

## Responsibilities
Run the quality commands selected from the active project profile.

## Profile-aware quality checks
Before running quality checks, read:
1. `artifacts/project_profile.json`
2. `.agent-project-profiles.yaml`

Use the selected project profile to choose validation commands.
Do not run hard-coded Django/Python checks unless the selected profile is `django`.
Do not run hard-coded Flowfox/Bun checks unless the selected profile is `flowfox`.

For `agent_workspace`, prefer:
- `make validate-artifacts`
- `make check`

For `django`, prefer:
- `ruff check .`
- `ruff format --check .`
- `mypy .`
- `pytest --tb=short --maxfail=1`

For `flowfox`, prefer:
- `bun node_modules/typescript/lib/tsc.js --noEmit`
- `git diff --check`
- focused tests when relevant
- studio TypeScript check when Studio files changed

If a command cannot be run because the environment is missing dependencies, record it as `not_run` with the reason. Do not invent successful results.

## Required JSON shape
```json
{
  "task": "",
  "project_profile": "agent_workspace|django|flowfox",
  "overall_status": "pass|warn|fail|not_run",
  "checks": [],
  "commands_attempted": [],
  "focused_tests_passed": true,
  "repository_checks_passed": true,
  "coverage": "not measured",
  "warnings": []
}
```

## Rules
- Preserve `PYTHONPATH=contactapi:contactapi/apps` only for Django repository commands that require it.
- Exclude local virtualenvs, media, artifacts, and migrations from linting noise where configured for the active profile.
- Record blockers rather than guessing if tooling is unavailable.
