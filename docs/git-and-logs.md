# Git, Docs, Logs, and Artifacts

## Git
- Git is the chronological record of agent work.
- Each GitHub issue should have its own branch, usually `issue/<number>-<short-name>` unless the project has stricter public naming rules.
- Each issue branch should have one matching journal: `docs/issues/issue-<number>.md`.
- `.agent-policy.yaml` is the source of truth for autonomy, publication, protected paths, and human approval gates.
- Commit, push, and PR creation stay manual unless the user explicitly asks or the target project's rules allow automated publication.
- For registered target projects, completed LOW/MEDIUM issue work may be automatically published after implementation, checks, risk classification, and required local visual evidence when `.agent-policy.yaml` and `.agent-repositories.yaml` allow it. Do not wait for a separate user approval unless the work is HIGH risk, touches protected paths, has unresolved blockers, or fails required checks without a recorded accepted blocker.
- Target project commits must use the repository's configured `git config user.name` and `git config user.email`. Do not hardcode or silently change identity; if either value is missing, stop and ask the user to configure it.
- Target project branch names are public PR metadata. Use branch prefixes allowed by `.agent-policy.yaml` or `.agent-repositories.yaml`, such as `feat/`, `fix/`, `issue/`, or `tast/`. Do not include agents, Codex, AI, automation, private control-plane paths, or internal tooling names.
- Target project commits and PRs must not mention agents, Codex, AI assistance, automation, private control-plane paths, `.agents`, `artifacts`, or `external/agents`. Write commit messages and PR text as ordinary product/engineering changes from the user's GitHub identity.
- Target project PRs must be created with the authenticated GitHub account already available to `git`/`gh`, and the body must include a sanitized product/engineering summary, checks, risk, blockers, and safe local screenshot/video/trace evidence references.
- Before staging target project work, inspect `git status --short` and stage every task-scoped changed/added/deleted public project file exactly once. Never stage or push `/Users/user/agents`, `external/agents/`, `.agents/`, `artifacts/`, private issue journals, private memory/wiki/graph files, prompt files, skills, audit logs, or sensitive screenshot/video/trace artifacts.
- Do not stage protected paths silently.
- Keep commits small enough to review.

## Local Visual Evidence
- For UI, routing, public CMS rendering, Studio UI, dashboard UI, or user-visible behavior, capture local-site evidence before publication.
- Prefer screenshots for static UI states and short videos or Playwright traces for multi-step flows, modals, animations, responsive behavior, or interactions.
- Evidence should point to local artifact paths and only safe PR-friendly screenshots when public sharing is appropriate. After publication, send the PR URL plus the local website URL where the completed issue can be checked. Do not publish screenshots containing private customer data, secrets, private URLs, internal issue notes, or any agent or control-plane references.
- If the local app cannot run or the affected route cannot be reached, record the blocker in `artifacts/report.md`, `artifacts/quality.json`, and the project issue journal.

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
