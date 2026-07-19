# AGENTS.md

## Mission
Build safe, reviewable improvements across supported project profiles using a policy-governed agent harness.

## Agent workspace model
- Treat this repository as the local home base for agents: prompts, skills, docs, logs, kanban boards, and audit artifacts live here.
- New agents should start with `docs/onboarding.md`, then read `AGENTS.md`, `docs/memory/lessons_learned.md`, and the current `.agent-runs/<run-id>/artifacts/plan.md`.
- Treat the active Harness home as a private control plane. Do not assume its memory files can be committed to any target project repository.
- Use git as the history of agent work. Keep changes small, reviewable, and traceable through `.agent-runs/<run-id>/audit-log.jsonl`.
- Store durable process documentation in `docs/`, not in one-off task artifacts.
- For multiple projects, keep private project memory under `docs/projects/<project>/`.
- Use markdown kanban boards under `docs/kanban/`:
  - `docs/kanban/tasks.md` for active execution work and process state.
  - `docs/kanban/tests-and-fixes.md` for failing checks, fixes, and retests.
  - `docs/kanban/features.md` for feature ideas and delivery slices.
- Keep per-issue execution history in `docs/projects/<project>/issues/issue-<number>.md`; every GitHub issue branch should have one matching issue journal.
- Maintain global agent knowledge in `docs/wiki/`, `docs/memory/`, and `docs/graph/`; maintain project-specific private knowledge in `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, and `docs/projects/<project>/graph/`.
- Use `docs/templates/goal.md` or the same structure in the current run's `artifacts/plan.md` before non-trivial implementation.
- Validate structured artifacts with `make validate-artifacts RUN_ID=<run-id>`; `make check` validates contracts and repository behavior.
- The expected output of autonomous work is a local git repository state with code, docs, logs, artifacts, and a clear verdict.

## Privacy and publication rules
- Private execution memory stays in the active Harness home by default.
- Target project repositories should receive only reviewed code, tests, migrations when explicitly allowed by the project rules, and safe public documentation.
- Do not publish `docs/projects/*/issues/`, `docs/projects/*/memory/`, `docs/projects/*/wiki/`, `docs/projects/*/graph/`, or `artifacts/` into a target project repository unless the user explicitly approves.
- Before copying any memory into a PR, issue comment, commit message, or project documentation, sanitize it: remove secrets, tokens, private customer data, internal reasoning traces, private URLs, and unnecessary names.
- Project privacy policy lives in `docs/projects/<project>/privacy.md` and must be read before work on that project.
- A GitHub issue is not worked automatically when it appears. Start issue work only when the user explicitly gives a project and issue number, unless the user has created a separate automation for monitoring.

## Autonomy and publication
- The source of truth for autonomy, publication, protected paths, and human approval gates is `.agent-policy.yaml`.
- If `AGENTS.md`, agent prompts, artifacts, or project docs conflict with `.agent-policy.yaml`, follow `.agent-policy.yaml`.
- Never auto-merge or deploy without explicit human approval.
- Never publish private control-plane files, private memory, issue journals, raw traces, secrets, or agent internals.

## Project profiles
- The source of truth for project-specific commands and validation strategy is `.agent-project-profiles.yaml`.
- Before planning, implementing, testing, reviewing, or publishing, agents must determine the active project profile:
  - `agent_workspace` for this agent control-plane repository;
  - `django` for Python / Django / DRF projects;
  - `nextjs_web` for generic Next.js / React / Prisma / Sanity / Bun work.
- Agents must not run Django/Python quality commands on `nextjs_web` tasks unless the task explicitly touches a Django project.
- Agents must not run Bun/Next.js commands on this agent workspace unless the task explicitly targets a registered web repository.
- The selected profile must be recorded under `.agent-runs/<run-id>/artifacts/project_profile.json` and referenced by the run-scoped quality, report, and verdict artifacts.

## Global operating rules
- Prefer minimal, reversible changes.
- Never claim success without running verification tools.
- Inspect existing code patterns before changing structure.
- Update tests and docs when behavior changes.
- Avoid broad rewrites unless absolutely necessary.
- Keep diffs reviewable.

## Project-specific rules
- For registered target projects, read `docs/projects/<project>/AGENTS.md` when it exists before planning, changing, checking, reviewing, or publishing project work.
- For project-specific durable knowledge, prefer `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, and `docs/projects/<project>/graph/` over expanding this root file.

## Required verification loop
For every non-trivial task:
1. inspect relevant files
2. create or update `.agent-runs/<run-id>/artifacts/plan.md`
3. classify risk in the same run directory
4. implement the minimal patch
5. run quality checks
6. run security checks
7. run tests
8. repair failures
9. re-run checks
10. update only artifacts owned by the active role
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
- `detect-secrets` and dependency audit must be part of the pipeline when the selected project profile requires them.

## Autonomy gates
- Follow `.agent-policy.yaml` for exact autonomy permissions by risk class and project.
- LOW risk: may patch locally and, when policy allows, automatically commit, push, and create or update a PR after required verification and local evidence.
- MEDIUM risk: may patch locally and, when policy allows, automatically commit, push, and create or update a PR after required verification and local evidence, provided protected paths are not touched and blockers are recorded.
- HIGH risk: may analyze and prepare a patch only, and must await human approval.
- Publication exception: completed LOW or MEDIUM work in a registered target repository does not need a separate approve/approved/аппрув/одобряю reply before commit, push, or PR creation when `.agent-policy.yaml` and `.agent-repositories.yaml` allow it. This exception never allows auto-merge, deployment, protected-path changes, secret publication, HIGH-risk publication, or unapproved scope expansion.
- Publication text rule: branch names, commit messages, branch descriptions, PR titles, PR bodies, issue comments, and release notes must follow the active project policy's `public_output_forbidden_phrases`. Product terms such as AI are allowed when they describe user-facing product behavior rather than the internal development process.

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
- All mutable task state lives only in `.agent-runs/<run-id>/`.
- `workflow.json`: authoritative workflow state.
- `context-manifests/`: compiled role context.
- `role-requests/` and `role-results/`: role execution contracts and results.
- `raw-events/`: raw executor events and JSONL usage evidence.
- `artifacts/`: owned role outputs including `plan.md`, `risk.json`, `quality.json`, `security.json`, `review.json`, `verdict.json`, and `publication.json` when publication runs.
- `metrics.json`: per-role duration and token usage.
- `errors.jsonl`: structured terminal errors and approval stops.
- `audit-log.jsonl`: autonomous publication actions.
- `.agent-artifact-owners.yaml` and `.agent-role-contracts.yaml` are the ownership source of truth.
- `.agent-routing.yaml` and `scripts/workflow_router.py` are authoritative for gates; role `next_action` is advisory.
- `.agent-tool-policy.yaml` is authoritative for role tool actions, side effects, domains, credential types, and timeouts.
- `.agent-queue/tasks.db` is scheduler state only. It must not duplicate mutable workflow or artifact state from `.agent-runs/<run-id>/`.
- Issue Intake is a deterministic `harness_stage` with `llm_invocation=false`; it must not be treated as an LLM role.
- Security routing is severity-aware: CRITICAL findings hard-block the workflow; MEDIUM and HIGH findings require human approval.
- Approval is a scoped lifecycle, not a status edit: request, approve/reject/expire, consume once, and resume the same run/worktree from its checkpoint. Resume is queued with the existing `run_id`.
- Worker leases preserve the assigned `run_id`; after a process dies, expiry requeues checkpoint resume instead of creating a fresh workflow state.
- External CLI, API, webhook, GitHub Issue, and CI events must normalize into the same task envelope before queueing.
- GitHub Actions repair intake requires a verified webhook signature, governed log access, secret redaction, and repair of the existing run/branch/PR.

## Artifact hygiene
- Do not create or mutate repository-root `artifacts/`; it is not workflow state.
- Keep each run's `artifacts/` small and current.
- Required artifacts should describe the current task only.
- Copy durable issue history and final summaries into `docs/projects/<project>/issues/issue-<number>.md`; do not rely on run artifacts as long-term issue memory.
- Move durable project knowledge into `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, `docs/projects/<project>/graph/`, or `docs/memory/lessons_learned.md`.
- Move only cross-project agent-system knowledge into global `docs/wiki/`, `docs/memory/`, or `docs/graph/`.
- Do not leave old probe scripts, large JSON dumps, or stale sweep reports in a run after their findings have been summarized.
- If a future task needs temporary investigation outputs, create them intentionally and remove or summarize them before completion.

## Lessons learned policy
- Agents must read `AGENTS.md` and `docs/memory/lessons_learned.md` before major conclusions.
- Recurring mistakes must be written into `docs/memory/lessons_learned.md`.
- Stable lessons may be summarized in Persistent repository rules.
- Completion must be rejected if a known past mistake reappears without explanation.

## Done criteria
A task is not done until:
- relevant checks passed or blockers are explicitly recorded
- risk is classified
- artifacts are updated
- tests are added or updated if needed
- for UI or user-visible work that requires visual evidence, local screenshot/video/trace evidence is captured, or a warning is recorded and any PR is draft when policy allows publication without evidence
- the next action is clearly stated
- an audit log entry is written for autonomous actions
- concurrent acceptance claims pass `make step2-verify` against real queued runs, not synthetic fixtures

## Persistent repository rules
- Treat any migration, auth, permission, session, CSRF, production settings, secret-management, webhook, payment, billing, or irreversible side-effect change as elevated risk.
- Keep local agent memory private; do not copy private issue journals into public PR text unless explicitly approved and sanitized.
- Route every model-backed role through the provider-neutral `Runtime` contract. In Step 2 only local-subscription `codex-cli` is a production runtime; direct provider SDK/CLI calls from Harness code and Model Router behavior are forbidden.
- Treat `.agent/project.yaml` as local execution identity only. It must never grant publication, merge, deployment, credential, network, protected-path, or provider-routing authority.
