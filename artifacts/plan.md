# Goal

## GOAL

- Harden autonomy and project profile contracts so LOW/MEDIUM work proceeds through commit, push, and PR publication while HIGH work stops for human approval.

## PROJECT_PROFILE

- Selected profile: `agent_workspace`
- Reason: this task changes the agent control-plane repository under `/Users/user/agents`, including prompts, schemas, artifacts, docs, and validation scripts.
- Quality commands: `make validate-artifacts`, `make check`
- Security commands: none required for `agent_workspace`; use the documentation-only `make security` placeholder and manual private-file/secrets review.
- Frontend evidence required: false
- Matched markers: `AGENTS.md`, `.agents/**`, `schemas/**`, `artifacts/**`, `scripts/validate_artifacts.py`, `Makefile`

## CONTEXT

- Previous policy used a combined publication permission instead of separate commit, push, open PR, and update PR permissions.
- Risk and verdict artifacts did not yet enforce high-risk triggers, publication status, PR state, or cross-artifact profile consistency.
- YAML policy files were only checked by marker strings instead of structural parsing.
- Implementation/test prompts still had residual global Django assumptions.

## CONSTRAINTS

- Do not add frontend QA agents, architecture-consistency agents, semantic-conflict agents, LangGraph, Hermes, OpenClaw, model routers, or broad eval banks.
- Do not auto-merge, deploy, force-push, rewrite history, or access production credentials.
- Keep changes limited to harness policy, prompts, schemas, validation, tests, docs, and artifacts.

## RISK

- MEDIUM: private agent harness contracts and validation semantics change future autonomy and publication behavior.

## PLAN

1. Replace the combined publication permission with separate `commit`, `push`, `open_pr`, and `update_pr` permissions in policy, risk prompt, schema, and artifact.
2. Update orchestrator actions and verdict schema/artifact to use `open_pr`, `update_pr`, `await_approval`, `reject`, and `no_changes`.
3. Add semantic validation for risk invariants, verdict invariants, cross-artifact profile/risk consistency, and structurally parsed YAML files.
4. Make implementation and test prompts fully project-profile aware.
5. Make `make check` robust outside git and add validator regression tests.
6. Update current quality, report, review, security, verdict, kanban, and audit artifacts.
7. Run profile-selected checks for `agent_workspace`: `make validate-artifacts`, validator pytest, `make security`, `make check`, and `git diff --check`.

## DONE WHEN

- The deprecated combined publication permission is not part of active policy, prompts, schemas, or artifacts.
- LOW/MEDIUM risk permits commit, push, open PR, and update PR; HIGH risk forbids them.
- `update_pr` is a distinct orchestrator action.
- Risk prompt/schema/artifact and orchestrator prompt/verdict schema/artifact use matching contracts.
- YAML files are parsed and validated structurally.
- Validator regression tests cover semantic invariants.
- `make validate-artifacts` and `make check` pass.

## VERIFY

- `make validate-artifacts`
- `make security`
- `make check`
- `git diff --check`

## STOP RULES

- Stop if the change would require editing target Flowfox application code, migrations, secrets, auth, billing, production infrastructure, or real GitHub publication.
