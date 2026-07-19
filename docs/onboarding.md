# Agent Onboarding

For ordinary project use, install the product CLI once with `pipx install .`, then run `agent init` inside the project. See `docs/cli.md`. The lower-level checklist below is for contributors changing the Harness itself.

Use this checklist before making changes.

## Entry Order
1. Read `AGENTS.md`.
2. Read `docs/memory/lessons_learned.md`.
3. Read `docs/index.md`.
4. Identify the target project. If the user did not provide one and the task is project-specific, ask for it.
5. Read `docs/projects/README.md`.
6. Read `docs/projects/<project>/privacy.md` before touching project-specific issue memory.
7. Read `docs/wiki/index.md` and only the global wiki pages relevant to the task.
8. Read `docs/projects/<project>/wiki/` and `docs/projects/<project>/memory/` when the task belongs to a project.
9. Read the relevant map in `docs/graph/` and `docs/projects/<project>/graph/`.
10. Read the relevant kanban board in `docs/kanban/`.
11. For GitHub issue work, read or create `docs/projects/<project>/issues/issue-<number>.md`.
12. Create one `.agent-runs/<run-id>/` and read or create its `artifacts/plan.md` using `docs/templates/goal.md`.
13. Classify risk in the same run's `artifacts/risk.json`.

## Operating Loop
1. Inspect only the files needed for the task.
2. Write a short plan.
3. Patch minimally.
4. Run focused checks.
5. Run quality checks when available.
6. Run security checks when available.
7. Update only the active role's run-scoped artifacts.
8. Run `make validate-artifacts RUN_ID=<run-id>` and `make check`.
9. Update the project issue journal if the task belongs to a GitHub issue.
10. Update project wiki, memory, or graph when durable project knowledge changed.
11. Update global wiki, memory, or graph only for cross-project agent-system knowledge.
12. Update the relevant kanban card.
13. Append `.agent-runs/<run-id>/audit-log.jsonl`.
14. Leave the next action explicit.

## Runtime Gate
Run these before building workflow routing or repair-loop changes on top of the production runtime:

```sh
make runtime-preflight
make codex-preflight
make codex-smoke
```

`.agent-runtime.yaml` selects the only Step 2 production runtime, `codex-cli`, using the local subscription with no API dependency. `make runtime-preflight` loads it through the runtime registry; `make codex-preflight` is its compatibility alias. The preflight checks that `codex exec` is available, authenticated, supports the required JSON/schema/output flags, can access the target repo, and can apply the requested sandbox. `make codex-smoke` runs the strict real-Codex planner smoke: `plan.md` and `project_profile.json` must be created, raw JSONL and token usage must be saved, and the read-only role must leave the repo unchanged.

Harness code may call only `Runtime.preflight(...)` and `Runtime.execute(...)`; provider commands and SDK calls belong inside runtime adapters. New provider adapters are Step 3 work. Model selection is Step 4 work and must not be added to the deterministic workflow router.

After smoke, Step 1 acceptance requires `make step1-verify RUN_ID=<evidence-run-id> STEP1_MANIFEST=<run-id-list-file>` against 10-20 real task runs.

## Concurrent Workflow Gate

Before starting workers, validate `.agent-routing.yaml`, `.agent-tool-policy.yaml`, and role contracts with `make validate-artifacts`. Enqueue idempotent task keys with `scripts/task_queue.py`, run 2–3 workers with `make queue-worker`, and inspect only exceptions with `make list-exceptions` or `scripts/list_runs.py` filters.

For long-lived operation use `make worker-service-start`, verify `make worker-service-health`, and stop with `make worker-service-stop`. Approval is never a direct status edit: use `make approve-run RUN_ID=... ACTOR=...`, then `make resume-run RUN_ID=...`; rejection requires an actor and reason. The loopback control API exposes the same transitions and compact metrics. GitHub Actions webhooks additionally require `AGENT_GITHUB_WEBHOOK_SECRET`; optional API bearer authentication uses `AGENT_CONTROL_PLANE_TOKEN`.

Step 2 acceptance requires `make step2-verify RUN_ID=<evidence-run-id> QUEUE_DB=<queue.db>`. The evidence must come from real Codex runs and include concurrent workers, isolated worktrees, governed tools, independent verification, a PR, and a human exception.

## Handoff Rules
- If implementation is incomplete, leave a concrete blocker in the current run's `artifacts/report.md` and `errors.jsonl`.
- If checks fail because of the repository baseline, separate baseline failures from task-specific failures.
- If a lesson repeats, update `docs/memory/lessons_learned.md`.
- If a task touches protected paths, stop at analysis and request human approval.
- Do not publish private project memory into a target project repository or PR unless the user explicitly approves a sanitized summary.

## What Good Looks Like
- The git diff is small.
- The current task is understandable from its `.agent-runs/<run-id>/`, the issue journal, and the kanban card.
- Durable new project knowledge is promoted from run artifacts into `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, or `docs/projects/<project>/graph/`.
- The repository contains the code output, docs, logs, and audit trail needed for review.
