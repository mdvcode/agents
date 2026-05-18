# AGENTS.md

## Mission
Build safe, reviewable improvements to this Django repository with minimal diffs, explicit verification, and repository-local artifacts that make autonomous work auditable.

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
11. decide the next action

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
