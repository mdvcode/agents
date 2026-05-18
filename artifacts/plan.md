TASK
- Analyze the local agent workspace, improve its operating structure, clean stale artifacts, and add durable per-issue history for GitHub issue work.

CONTEXT
- User requested an agent setup matching the screenshot: local git history, docs store with logs, kanban boards for tasks/process, tests/fixes, features, onboarding for agents, and local repo output.
- User clarified that every GitHub issue should have visible execution history, while every issue also has its own branch.
- Repository already contains `.agents/prompts/`, `.agents/skills/`, `AGENTS.md`, git history, and `artifacts/`.
- `artifacts/` contained required runtime artifacts plus stale one-off sweep/probe files from previous Django investigations.

FILES_TO_INSPECT
- `AGENTS.md`
- `artifacts/lessons_learned.md`
- `.agents/prompts/*.md`
- `.agents/skills/*/SKILL.md`
- `artifacts/`

FILES_TO_CHANGE
- `AGENTS.md`
- `docs/index.md`
- `docs/onboarding.md`
- `docs/git-and-logs.md`
- `docs/agent-system.md`
- `docs/kanban/tasks.md`
- `docs/kanban/tests-and-fixes.md`
- `docs/kanban/features.md`
- `docs/issues/README.md`
- `docs/issues/_template.md`
- Required files under `artifacts/`

DO_NOT_TOUCH
- Django application code.
- Protected paths from `AGENTS.md`.
- Existing user-added `.idea/*` changes.

ASSUMPTIONS
- Cleaning artifacts means removing stale temporary investigation outputs while preserving required current-task artifacts, lessons, and audit log.
- Documentation and process-only changes are sufficient; no Django behavior change is needed.
- Per-issue history should live in `docs/issues/` because issue history is durable, while `artifacts/` is current-task state.

CHECKS_TO_RUN
- `git status --short`
- `find artifacts -maxdepth 1 -type f | sort`
- `python3 -m json.tool artifacts/risk.json`
- `python3 -m json.tool artifacts/quality.json`
- `python3 -m json.tool artifacts/verdict.json`
- `git diff --check`
- `make check`
- `make security`

INITIAL_RISK_CLASS
- LOW: documentation, prompts/process, and artifact hygiene only.

DONE_CRITERIA
- Agents have a clear onboarding path.
- Git/docs/logs/artifacts/kanban expectations are documented.
- Kanban boards exist for tasks/process, tests/fixes, and features.
- Per-issue history convention and template exist for GitHub issue branches.
- `artifacts/` contains only required current-task artifacts, lessons, and audit log.
- Verification outcomes and blockers are recorded.
