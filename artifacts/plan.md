# Goal

## GOAL

- Add Flowfox publication rules so commits/PRs do not mention agents and private agent/control-plane files are never committed or pushed.

## CONTEXT

- User wants Flowfox commit messages and PR text to avoid mentioning agents, Codex, AI assistance, or automation.
- User also wants agent/control-plane files excluded from commits, pushes, and PRs.
- Existing Flowfox approve-gated publishing rules already require explicit approval before commit/push/PR.

## CONSTRAINTS

- Do not hardcode git `user.name` or `user.email`.
- Do not allow commit/push/PR before explicit approval.
- Do not publish private screenshots, issue notes, secrets, internal memory, agent files, or control-plane paths.
- Do not mention agents, Codex, AI assistance, automation, `.agents`, `artifacts`, `external/agents`, or `/Users/user/agents` in public Flowfox commit/PR text.
- Do not allow auto-merge, deploy, protected-path changes, or scope expansion.

## RISK

- LOW: private agent process documentation and prompt guidance only.

## PLAN

1. Add no-agent-wording and no-control-plane-files rules to `AGENTS.md`.
2. Update git workflow docs and skills.
3. Update orchestrator guidance and workflow/agent graph docs.
4. Update Flowfox privacy notes.
5. Run artifact validation and diff hygiene checks.

## DONE WHEN

- Agents are instructed to exclude private agent/control-plane files before staging Flowfox work.
- Agents are instructed to keep commit messages, PR titles/bodies, issue comments, and release notes free of agent/Codex/AI/automation wording.
- Existing approval-gated publish flow remains intact.
- Privacy and protected-path limits remain intact.

## VERIFY

- `make validate-artifacts`
- `git diff --check`

## STOP RULES

- Stop if the change would require editing target Flowfox application code, migrations, secrets, auth, billing, production infrastructure, or real GitHub publication.
