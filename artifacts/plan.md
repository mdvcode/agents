# Goal

## GOAL

- Normalize the agent autonomy contract so non-HIGH Flowfox work can automatically commit, push, and create/update a PR after required gates pass.

## CONTEXT

- User corrected the proposed hybrid model: LOW and MEDIUM Flowfox work should both be able to commit, push, and create/update a PR autonomously.
- User wants every task-scoped changed/added/deleted file included in publication after completion.
- User wants commits and PRs to use the repository's configured `user.name` / `user.email` and the authenticated GitHub account.
- User wants no public mention of agents, Codex, AI assistance, or automation.
- User wants public branch/commit/PR metadata to avoid agent/Codex/AI/automation wording.
- User wants a local website URL after publication so the issue can be checked.
- Existing orchestrator and git workflow guidance still require explicit approval before commit/push/PR and must be changed.
- Risk and verdict schemas are too weak to enforce structured autonomy decisions.

## CONSTRAINTS

- Do not hardcode git `user.name` or `user.email`.
- Do not auto-publish HIGH-risk or protected-path work.
- Do not publish private screenshots, issue notes, secrets, internal memory, agent files, or control-plane paths.
- Do not mention agents, Codex, AI assistance, automation, `.agents`, `artifacts`, `external/agents`, or `/Users/user/agents` in public Flowfox commit/PR text.
- Do not allow auto-merge, deploy, protected-path changes, or scope expansion.

## RISK

- MEDIUM: private agent process documentation and schemas change future publication behavior by allowing non-HIGH Flowfox completed issue work to publish autonomously.

## PLAN

1. Add `.agent-policy.yaml` as the machine-readable source of truth for autonomy, publication, protected paths, and human approval gates.
2. Align `AGENTS.md`, orchestrator prompt, repo policy skill, and git workflow skill with the policy.
3. Set Flowfox LOW and MEDIUM policy to allow commit/push and PR creation after required gates pass; keep HIGH/protected/manual blockers.
4. Strengthen risk and verdict schemas plus artifact validation for required types and nested fields.
5. Update current task artifacts and durable workflow/privacy docs.
6. Run artifact validation, security placeholder, repository check, and diff hygiene checks.

## DONE WHEN

- `.agent-policy.yaml` exists and is the source of truth for autonomy/publication gates.
- Flowfox completed LOW/MEDIUM issue work no longer waits for a separate user approval before commit/push/PR when checks/evidence/protected-path gates pass.
- All task-scoped changed/added/deleted public project files are included in the commit.
- Commit/PR publication uses configured git identity and authenticated GitHub account.
- Public publication wording remains free of agent/Codex/AI/automation references.
- Public Flowfox branch names also remain free of agent/Codex/AI/automation references.
- Privacy, protected-path, HIGH-risk, no-auto-merge, and no-deploy limits remain intact.
- Risk/verdict artifacts use strict structured shapes.
- Durable docs no longer contradict `.agent-policy.yaml` about Flowfox automated publication.

## VERIFY

- `make validate-artifacts`
- `make security`
- `make check`
- `git diff --check`

## STOP RULES

- Stop if the change would require editing target Flowfox application code, migrations, secrets, auth, billing, production infrastructure, or real GitHub publication.
