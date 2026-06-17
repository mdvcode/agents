# Goal

## GOAL

- Add project profiles for the agent workspace, Django, and Flowfox so planning, tests, quality checks, security checks, review, report, and publication gates are profile-aware.

## PROJECT_PROFILE

- Selected profile: `agent_workspace`
- Reason: this task changes the agent control-plane repository under `/Users/user/agents`, including prompts, schemas, artifacts, docs, and validation scripts.
- Quality commands: `make validate-artifacts`, `make check`
- Security commands: none required for `agent_workspace`; use the documentation-only `make security` placeholder and manual private-file/secrets review.
- Frontend evidence required: false
- Matched markers: `AGENTS.md`, `.agents/**`, `schemas/**`, `artifacts/**`, `scripts/validate_artifacts.py`, `Makefile`

## CONTEXT

- Current agent prompts are partly Django-centric and can choose Python/Django checks for Flowfox tasks.
- Flowfox needs Next.js / React / Prisma / Sanity / Bun / TypeScript checks, not default Django tooling.
- This agent workspace needs artifact/schema validation, not Django or Flowfox application checks.
- The selected project profile must be recorded in `artifacts/project_profile.json` and reflected in quality, report, and verdict artifacts.

## CONSTRAINTS

- Do not add new agents, model routers, frontend QA agents, eval runners, LangGraph, Hermes, or OpenClaw.
- Do not run Django/Python checks for the agent workspace task.
- Do not run Flowfox/Bun checks for the agent workspace task.
- Keep changes limited to harness policy, prompts, schemas, docs, validation, and artifacts.

## RISK

- MEDIUM: private agent process prompts and schemas change future command selection and publication gates across project types.

## PLAN

1. Add `.agent-project-profiles.yaml` with `agent_workspace`, `django`, and `flowfox` profiles.
2. Add `artifacts/project_profile.json` and `schemas/project_profile.schema.json`.
3. Update artifact validation and `make check` to validate the project profile artifact.
4. Update `AGENTS.md` and planner, quality, test, security, review, report, and orchestrator prompts to use profiles.
5. Update `schemas/quality.schema.json` and `artifacts/quality.json` to include `project_profile` and `commands_attempted`.
6. Update current risk, report, review, security, verdict, kanban, and audit artifacts.
7. Run profile-selected checks for `agent_workspace`: `make validate-artifacts`, `make security`, `make check`, and `git diff --check`.

## DONE WHEN

- `.agent-project-profiles.yaml` exists and defines `agent_workspace`, `django`, and `flowfox`.
- `artifacts/project_profile.json` records `agent_workspace` for this task.
- `schemas/project_profile.schema.json` and `schemas/quality.schema.json` enforce profile-aware artifact fields.
- Planner, quality runner, test generator, security agent, reviewer, report agent, and orchestrator prompts are profile-aware.
- Flowfox tasks are guided toward Bun/TypeScript/focused tests and visual evidence rather than Django checks.
- Agent workspace tasks are guided toward artifact/schema validation.
- `make validate-artifacts` and `make check` pass.

## VERIFY

- `make validate-artifacts`
- `make security`
- `make check`
- `git diff --check`

## STOP RULES

- Stop if the change would require editing target Flowfox application code, migrations, secrets, auth, billing, production infrastructure, or real GitHub publication.
