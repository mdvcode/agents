# AGENTS.md

## Mission
Build safe, reviewable improvements to this Django repository with minimal diffs, explicit verification, and repository-local artifacts that make autonomous work auditable.

## Agent workspace model
- Treat this repository as the local home base for agents: prompts, skills, docs, logs, kanban boards, and audit artifacts live here.
- New agents should start with `docs/onboarding.md`, then read `AGENTS.md`, `artifacts/lessons_learned.md`, and the current `artifacts/plan.md`.
- Treat `/Users/user/agents` as a private control plane. Do not assume its memory files can be committed to any target project repository.
- Use git as the history of agent work. Keep changes small, reviewable, and traceable through `artifacts/audit_log.jsonl`.
- Store durable process documentation in `docs/`, not in one-off task artifacts.
- For multiple projects, keep private project memory under `docs/projects/<project>/`.
- Use markdown kanban boards under `docs/kanban/`:
  - `docs/kanban/tasks.md` for active execution work and process state.
  - `docs/kanban/tests-and-fixes.md` for failing checks, fixes, and retests.
  - `docs/kanban/features.md` for feature ideas and delivery slices.
- Keep per-issue execution history in `docs/projects/<project>/issues/issue-<number>.md`; every GitHub issue branch should have one matching issue journal.
- Maintain global agent knowledge in `docs/wiki/`, `docs/memory/`, and `docs/graph/`; maintain project-specific private knowledge in `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, and `docs/projects/<project>/graph/`.
- Use `docs/templates/goal.md` or the same structure in `artifacts/plan.md` before non-trivial implementation.
- Validate structured artifacts with `make validate-artifacts` or `make check`.
- The expected output of autonomous work is a local git repository state with code, docs, logs, artifacts, and a clear verdict.

## Privacy and publication rules
- Private execution memory stays in `/Users/user/agents` by default.
- Target project repositories should receive only reviewed code, tests, migrations when explicitly approved, and safe public documentation.
- Do not publish `docs/projects/*/issues/`, `docs/projects/*/memory/`, `docs/projects/*/wiki/`, `docs/projects/*/graph/`, or `artifacts/` into a target project repository unless the user explicitly approves.
- Before copying any memory into a PR, issue comment, commit message, or project documentation, sanitize it: remove secrets, tokens, private customer data, internal reasoning traces, private URLs, and unnecessary names.
- Project privacy policy lives in `docs/projects/<project>/privacy.md` and must be read before work on that project.
- A GitHub issue is not worked automatically when it appears. Start issue work only when the user explicitly gives a project and issue number, unless the user has created a separate automation for monitoring.

## Global operating rules
- Prefer minimal, reversible changes.
- Never claim success without running verification tools.
- Inspect existing code patterns before changing structure.
- Update tests and docs when behavior changes.
- Avoid broad rewrites unless absolutely necessary.
- Keep diffs reviewable.

## Required verification loop
For every non-trivial task:
1. inspect relevant files
2. create or update `artifacts/plan.md`
3. classify risk
4. implement the minimal patch
5. run quality checks
6. run security checks
7. run tests
8. repair failures
9. re-run checks
10. update artifacts
11. update the project issue journal and private project memory/wiki/graph when durable project knowledge changed
12. decide the next action

## Python rules
- Prefer explicit typing where practical.
- Avoid mutable default arguments.
- Avoid broad `except` blocks.
- Avoid hidden side effects.
- Avoid dead code and speculative abstraction.
- Keep functions focused and cohesive.

## Django rules
- Do not place new business logic in views, admin classes, serializers, or forms unless that pattern is already clearly established nearby.
- Prefer service or domain-style helpers for new business logic.
- When logic clearly belongs to one model's state or behavior, prefer a member method or property on that model over introducing a new module-level helper function.
- Keep database access consistent with repository patterns.
- Avoid N+1 queries with `select_related` and `prefetch_related` where appropriate.
- Respect the existing settings module structure and `manage.py` boot path.
- Do not change migrations autonomously.
- Do not modify management commands destructively without explicit safety notes.

## DRF/API rules
- Public endpoints must have clear validation.
- Serializer validation must be explicit.
- Authentication and permission changes are high risk.
- Preserve backward compatibility unless the task explicitly requires behavior change.
- Prefer typed service functions and narrow serializer responsibilities.

## Test requirements
- Every new public behavior should have at least one test unless clearly impossible.
- Prefer pytest execution that remains compatible with the repository's existing Django `TestCase` style.
- Reuse fixtures and factories where possible.
- Include regression tests for bug fixes.
- Target 80 percent coverage unless the repository already defines a different threshold.

## Security rules
- No hardcoded secrets.
- No `shell=True`.
- No `eval` or `exec`.
- Validate external input.
- Use the ORM or parameterized queries only.
- Do not touch auth, billing, secrets, or production infrastructure autonomously.
- `detect-secrets` and dependency audit must be part of the pipeline.

## Autonomy gates
- LOW risk: may patch locally. Commit, push, and PR creation or updates should remain manual unless explicitly requested.
- MEDIUM risk: may patch and prepare a PR update, but no autonomous commit, auto-merge, or deploy.
- HIGH risk: may analyze and prepare a patch only, and must await human approval.

## Denylist and protected paths
Treat changes touching any of the following as HIGH risk and protected:
- `**/.env`
- `**/.env.*`
- `**/*.pem`
- `**/*.key`
- `**/*secret*`
- `**/migrations/**`
- `**/infra/prod/**`
- `**/terraform/**`
- `**/k8s/prod/**`
- `**/auth/**`
- `**/billing/**`
- `**/payments/**`
- `**/credentials/**`
- `**/secrets/**`
- `**/settings_prod.py`
- `**/settings/production.py`
- deployment scripts that affect production directly

## Required artifacts
- `artifacts/plan.md`
- `artifacts/risk.json`
- `artifacts/review.md`
- `artifacts/quality.json`
- `artifacts/security.md`
- `artifacts/verdict.json`
- `artifacts/report.md`
- `artifacts/lessons_learned.md`
- `artifacts/audit_log.jsonl`

## Artifact hygiene
- Keep `artifacts/` small and current.
- Required artifacts should describe the current task only.
- Copy durable issue history and final summaries into `docs/projects/<project>/issues/issue-<number>.md`; do not rely on `artifacts/` as long-term issue memory.
- Move durable project knowledge into `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, `docs/projects/<project>/graph/`, or `artifacts/lessons_learned.md`.
- Move only cross-project agent-system knowledge into global `docs/wiki/`, `docs/memory/`, or `docs/graph/`.
- Do not leave old probe scripts, large JSON dumps, or stale sweep reports in `artifacts/` after their findings have been summarized.
- If a future task needs temporary investigation outputs, create them intentionally and remove or summarize them before completion.

## Lessons learned policy
- Agents must read `AGENTS.md` and `artifacts/lessons_learned.md` before major conclusions.
- Recurring mistakes must be written into `artifacts/lessons_learned.md`.
- Stable lessons may be summarized in Persistent repository rules.
- Completion must be rejected if a known past mistake reappears without explanation.

## Done criteria
A task is not done until:
- relevant checks passed or blockers are explicitly recorded
- risk is classified
- artifacts are updated
- tests are added or updated if needed
- the next action is clearly stated
- an audit log entry is written for autonomous actions

## Persistent repository rules
- Preserve the current Django layout rooted at `contactapi/manage.py` and `contactapi/contactapi/`.
- Preserve the repository's current Django `TestCase`-centric test style while enabling pytest-based execution.
- Treat any migration, auth, permission, session, JWT, CSRF, admin bulk action, destructive queryset, production settings, secret-management, webhook, payment, billing, or irreversible side-effect change as elevated risk.
- Treat Celery task behavior changes as at least MEDIUM risk.
- Review Django admin performance-sensitive changes for queryset optimization and pagination impact.
- Prefer model member methods over small helper functions when the behavior belongs to a specific repository model.
