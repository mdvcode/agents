# Goal

## GOAL

- Update Flowfox publication rules so completed issue work is automatically committed, pushed, and opened/updated as a PR from the user's configured git/GitHub identity without a separate approval step.

## CONTEXT

- User wants every task-scoped changed/added/deleted file included in publication after completion.
- User wants commits and PRs to use the repository's configured `user.name` / `user.email` and the authenticated GitHub account.
- User wants no public mention of agents, Codex, AI assistance, or automation.
- User wants public branch/commit/PR metadata to avoid agent/Codex/AI/automation wording.
- User wants a local website URL after publication so the issue can be checked.
- Existing Flowfox rules currently require explicit approval before commit/push/PR and must be changed.

## CONSTRAINTS

- Do not hardcode git `user.name` or `user.email`.
- Do not auto-publish HIGH-risk or protected-path work.
- Do not publish private screenshots, issue notes, secrets, internal memory, agent files, or control-plane paths.
- Do not mention agents, Codex, AI assistance, automation, `.agents`, `artifacts`, `external/agents`, or `/Users/user/agents` in public Flowfox commit/PR text.
- Do not allow auto-merge, deploy, protected-path changes, or scope expansion.

## RISK

- MEDIUM: private agent process documentation changes future Flowfox publication behavior by removing the approval gate for LOW/MEDIUM completed issue work.

## PLAN

1. Replace the Flowfox explicit approval gate in `AGENTS.md` with an automated publication gate for completed LOW/MEDIUM issue work.
2. Preserve configured git identity, authenticated GitHub account usage, sanitized PR wording, private-file exclusions, and HIGH/protected stop rules.
3. Require public-safe Flowfox branch names without agent/Codex/AI/automation wording.
4. Require the final response after Flowfox publication to include the PR URL and local website URL for checking the issue.
5. Update durable workflow/privacy docs that still describe the old approve-gated Flowfox publication path.
6. Update current task artifacts.
7. Run artifact validation and diff hygiene checks.

## DONE WHEN

- Flowfox completed LOW/MEDIUM issue work no longer waits for a separate user approval before commit/push/PR.
- All task-scoped changed/added/deleted public project files are included in the commit.
- Commit/PR publication uses configured git identity and authenticated GitHub account.
- Public publication wording remains free of agent/Codex/AI/automation references.
- Public Flowfox branch names also remain free of agent/Codex/AI/automation references.
- Privacy, protected-path, HIGH-risk, no-auto-merge, and no-deploy limits remain intact.
- Durable docs no longer contradict `AGENTS.md` about Flowfox automated publication.

## VERIFY

- `make validate-artifacts`
- `git diff --check`

## STOP RULES

- Stop if the change would require editing target Flowfox application code, migrations, secrets, auth, billing, production infrastructure, or real GitHub publication.
