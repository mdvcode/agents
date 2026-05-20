# Git, Docs, Logs, and Artifacts

## Git
- Git is the chronological record of agent work.
- Each GitHub issue should have its own branch, usually `codex/issue-<number>-<short-name>`.
- Each issue branch should have one matching journal: `docs/issues/issue-<number>.md`.
- Commit, push, and PR creation stay manual unless the user explicitly asks.
- Do not stage protected paths silently.
- Keep commits small enough to review.

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
