# Git, Docs, Logs, and Artifacts

## Git
- Git is the chronological record of agent work.
- Each GitHub issue should have its own branch, usually `codex/issue-<number>-<short-name>`.
- Each issue branch should have one matching journal: `docs/issues/issue-<number>.md`.
- Commit, push, and PR creation stay manual unless the user explicitly asks.
- For Flowfox, the normal handoff is approve-gated: after implementation, checks, and local visual evidence, stop and request approval. Only after the user explicitly approves the completed state may the agent commit, push, and create or update the PR.
- Flowfox commits must use the repository's configured `git config user.name` and `git config user.email`. Do not hardcode or silently change identity; if either value is missing, stop and ask the user to configure it.
- Flowfox commits and PRs must not mention agents, Codex, AI assistance, automation, private control-plane paths, `.agents`, `artifacts`, or `external/agents`. Write commit messages and PR text as ordinary product/engineering changes from the user's GitHub identity.
- Flowfox PRs must be created with the authenticated GitHub account already available to `git`/`gh`, and the body must include a sanitized product/engineering summary, checks, risk, blockers, and safe local screenshot/video/trace evidence references.
- Before staging Flowfox work, inspect `git status --short` and stage only approved public project files. Never stage or push `/Users/user/agents`, `external/agents/`, `.agents/`, `artifacts/`, private issue journals, private memory/wiki/graph files, prompt files, skills, audit logs, or sensitive screenshot/video/trace artifacts.
- Do not stage protected paths silently.
- Keep commits small enough to review.

## Flowfox Local Evidence
- For UI, routing, public CMS rendering, Studio UI, dashboard UI, or user-visible behavior, capture local-site evidence before asking for approval.
- Prefer screenshots for static UI states and short videos or Playwright traces for multi-step flows, modals, animations, responsive behavior, or interactions.
- Evidence should point to local artifact paths for approval and only safe PR-friendly screenshots when public sharing is appropriate. Do not publish screenshots containing private customer data, secrets, private URLs, internal issue notes, or any agent/control-plane references.
- If the local app cannot run or the affected route cannot be reached, record the blocker in `artifacts/report.md`, `artifacts/quality.json`, and the Flowfox issue journal.

## Documentation Store
- Put durable process documentation in `docs/`.
- Put per-issue execution history in `docs/issues/`.
- Put synthesized reusable knowledge in `docs/wiki/`.
- Put cross-issue memory, daily notes, topic notes, and scratchpad items in `docs/memory/`.
- Put navigation maps in `docs/graph/`.
- Put agent prompts in `.agents/prompts/`.
- Put reusable local skills in `.agents/skills/`.
- Put task runtime outputs in `artifacts/`.

## Logs
- Use `artifacts/audit_log.jsonl` for append-only autonomous action history.
- Each entry should include time, agent, action, verdict, and whether focused checks passed.
- Keep large raw logs out of `artifacts/` unless they are necessary for current review.

## Artifact Cleanup
- Required artifacts are the only files that should remain in `artifacts/` by default.
- Summarize old probes and sweeps into `artifacts/report.md`, `artifacts/review.md`, or durable docs before removing raw files.
- For GitHub issue work, copy the final result, checks, blockers, and PR link into the issue journal before clearing `artifacts/`.
- Promote stable lessons to `docs/wiki/` or `docs/memory/`; do not make future agents rediscover them from raw logs.
- Temporary scripts should be deleted once their results are captured.
