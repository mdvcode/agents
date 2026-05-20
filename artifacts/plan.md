TASK
- Correct the agent system for multiple projects and private project memory.

CONTEXT
- User clarified that there are multiple projects and that issue execution history is private.
- The local `/Users/user/agents` repository should be a private control plane, not something copied into target project repositories.
- Project-specific issue journals, memory, wiki, and graph should be separated per project under `docs/projects/<project>/`.
- Target project GitHub repositories should receive only reviewed code/tests/public docs and sanitized summaries.
- GitHub issues should not be solved automatically when they appear; work starts when the user gives a project and issue number unless a separate monitor automation is requested.

FILES_TO_INSPECT
- `AGENTS.md`
- `artifacts/lessons_learned.md`
- `.agents/skills/*/SKILL.md`
- `docs/`
- `artifacts/`

FILES_TO_CHANGE
- `AGENTS.md`
- `docs/index.md`
- `docs/onboarding.md`
- `docs/git-and-logs.md`
- `docs/agent-system.md`
- `docs/wiki/`
- `docs/memory/`
- `docs/graph/`
- `docs/projects/`
- `docs/templates/goal.md`
- `docs/kanban/tasks.md`
- `docs/kanban/tests-and-fixes.md`
- `docs/kanban/features.md`
- `docs/issues/README.md`
- `docs/issues/_template.md`
- `.agents/skills/issue-intake/SKILL.md`
- `.agents/skills/context-engineering/SKILL.md`
- `.agents/skills/structured-output-guard/SKILL.md`
- `.agents/skills/performance-optimization/SKILL.md`
- `.agents/skills/documentation-and-adrs/SKILL.md`
- `schemas/`
- `scripts/validate_artifacts.py`
- `Makefile`
- Required files under `artifacts/`

DO_NOT_TOUCH
- Django application code.
- Protected paths from `AGENTS.md`.
- Existing user-added `.idea/*` changes.

ASSUMPTIONS
- Documentation/process changes are sufficient; no target project code changes are needed.
- Project memory is private by default and remains local under `/Users/user/agents`.
- Global `docs/wiki` and `docs/memory` should hold only cross-project agent-system knowledge, not private project issue details.

CHECKS_TO_RUN
- `git status --short`
- `find artifacts -maxdepth 1 -type f | sort`
- `python3 -m json.tool artifacts/risk.json`
- `python3 -m json.tool artifacts/quality.json`
- `python3 -m json.tool artifacts/verdict.json`
- `python3 scripts/validate_artifacts.py`
- `git diff --check`
- `make check`
- `make security`

INITIAL_RISK_CLASS
- LOW: documentation, local agent process, schemas, and lightweight validation only.

DONE_CRITERIA
- `AGENTS.md` states private control-plane and publication rules.
- `docs/projects/` template exists for multiple projects.
- Issue journals are project-scoped under `docs/projects/<project>/issues/`.
- Project privacy policy template exists.
- Skills and onboarding require project identification and privacy review.
- Verification outcomes and blockers are recorded.
