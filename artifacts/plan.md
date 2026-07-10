TASK
- P3.1f: remove obsolete project coupling from the agent control plane.

PROJECT_PROFILE
- Selected profile: agent_workspace.
- Reason: task changes the local agent harness policy, profiles, registry, schemas, scripts, prompts, docs, tests, and root artifacts.
- Quality commands: focused pytest for artifact validation, context compiler, publication, and security scan tests; full pytest; `make validate-artifacts`; `make check`.
- Security commands: `make security`.
- Frontend evidence required: no.

CONTEXT
- The control plane still has hard-coded obsolete project references in policy, project profiles, repository registry, schemas, publication/security/context scripts, prompts, docs, tests, and stale root artifacts.
- Use a generic `nextjs_web` project profile instead of a project-specific web profile.
- Rename the legacy project-specific visual evidence key to `visual_evidence`.
- Root `artifacts/` should describe the current task only; stale project history and screenshots should be removed or reset.
- Untracked `tmp/` and `output/` are transient local outputs, not control-plane state. Ignore those directories instead of mutating user scratch files.

FILES_TO_INSPECT
- `.agent-policy.yaml`
- `.agent-project-profiles.yaml`
- `.agent-repositories.yaml`
- `schemas/*.json`
- `schemas/roles/*.json`
- `scripts/*.py`
- `.agents/prompts/*.md`
- `.agents/skills/git-workflow/SKILL.md`
- `AGENTS.md`
- `docs/**/*.md`
- `tests/*.py`
- `artifacts/*`

FILES_TO_CHANGE
- `.gitignore`
- `.agent-policy.yaml`
- `.agent-project-profiles.yaml`
- `.agent-repositories.yaml`
- `schemas/project_profile.schema.json`
- `schemas/quality.schema.json`
- `schemas/verdict.schema.json`
- `schemas/change_set.schema.json`
- `schemas/roles/quality-runner.schema.json`
- `scripts/agent_role_runner.py`
- `scripts/context_compiler.py`
- `scripts/publish_pr.py`
- `scripts/repository_registry.py`
- `scripts/security_scan.py`
- `scripts/validate_artifacts.py`
- `.agents/prompts/implementation-agent.md`
- `.agents/prompts/orchestrator.md`
- `.agents/prompts/planner.md`
- `.agents/prompts/quality-runner.md`
- `.agents/prompts/reviewer.md`
- `.agents/prompts/security-agent.md`
- `.agents/prompts/test-generator.md`
- `.agents/skills/git-workflow/SKILL.md`
- `AGENTS.md`
- `docs/git-and-logs.md`
- `docs/graph/agents.md`
- `docs/graph/workflows.md`
- `docs/kanban/tasks.md`
- obsolete `docs/projects/<project>/**` project-memory directory
- `artifacts/*`
- `tests/test_validate_artifacts.py`
- `tests/test_context_compiler.py`
- `tests/test_publish_pr.py`
- `tests/test_security_scan.py`
- `tests/test_agent_role_runner.py`
- `tests/test_full_agent_workflow.py`

DO_NOT_TOUCH
- Do not mutate untracked personal scratch outputs in `tmp/` or `output/`; ignore them as transient local directories.
- Do not add a replacement real repository record until a new project is actually registered.
- Do not publish private project history into public docs.

ASSUMPTIONS
- Keep a generic `nextjs_web` profile because web/frontend QA and visual evidence rules are still useful for future projects.
- Root project memory for the obsolete project should be deleted because it is stale private coupling, not reusable generic knowledge.

CHECKS_TO_RUN
- run the obsolete-project-name grep over the repository with hidden files enabled and `.git`/`__pycache__` excluded
- `make validate-artifacts`
- `make security`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_validate_artifacts.py tests/test_context_compiler.py tests/test_publish_pr.py tests/test_security_scan.py tests/test_agent_role_runner.py tests/test_full_agent_workflow.py -q`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests`
- `make check`

INITIAL_RISK_CLASS
- Medium.
- Rationale: this is a broad control-plane cleanup touching policy, schemas, scripts, tests, and tracked private artifact/docs state. It does not touch auth, billing, migrations, production settings, deployment infrastructure, or secrets.

DONE_CRITERIA
- No obsolete project references remain in the control plane grep target.
- Repository registry contains only current registered repositories.
- Project profiles use `agent_workspace`, `django`, and `nextjs_web`.
- Policy uses generic project/profile rules and generic `visual_evidence`.
- Schemas, scripts, prompts, docs, tests, and artifacts use project-agnostic wording.
- Root artifacts are current-task-only and validation/security/relevant tests pass.
