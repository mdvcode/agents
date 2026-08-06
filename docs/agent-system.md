# Agent System Analysis

## Product UX

The control plane is packaged as `ai-harness` and exposes project intake/status/doctor commands plus structured failure, retry, resume, abort, and dead-letter recovery commands. One pipx installation can serve multiple repositories without submodules. Each initialized repository owns `.agent/project.yaml`; this config selects local execution identity and the existing Codex CLI runtime, while central Harness policy continues to own publication, recovery policy, and side-effect authorization.

## Current State
- The repository has a good role split: planner, risk classifier, implementation agent, test generator, quality runner, security agent, reviewer, report agent, and orchestrator.
- The skills are clear and useful, especially the Django, DRF, security, git, testing, and lessons policies.
- Each task now has one authoritative `.agent-runs/<run-id>/` containing workflow state, context manifests, role requests/results, raw events, owned artifacts, metrics, errors, and publication audit state.
- Every model-backed role receives one token-bounded Context Package compiled by Context Engine from static knowledge sources. Runtime adapters do not read Obsidian, skills, policies, contracts, or artifacts as independent prompt inputs.
- Repository-root `artifacts/` was removed; it is no longer a mutable compatibility path.
- Artifact ownership is declared in `.agent-artifact-owners.yaml` and enforced after every role.

## Improvements Made
- Added an explicit agent workspace model to `AGENTS.md`.
- Added onboarding docs so any agent can enter the repo in the same order.
- Added a docs index and a git/logs/artifacts policy.
- Added three kanban boards:
  - task and process execution;
  - tests and fixes;
  - features.
- Added a durable per-issue journal convention so every GitHub issue can keep its own timeline while `artifacts/` remains current-task scratch space.
- Cleaned stale runtime artifacts so `artifacts/` returns to its intended purpose: current task artifacts plus lessons and audit log.
- Added the PDF-derived architecture:
  - `docs/wiki/` for LLM Wiki-style durable knowledge.
  - `docs/memory/` for persistent memory, daily notes, scratchpad, and topics.
  - `docs/graph/` for project maps.
  - `docs/templates/goal.md` for `/goal` scoping.
  - `schemas/`, `scripts/validate_artifacts.py`, and `Makefile` for structured output guardrails.
  - New skills for issue intake, context engineering, structured output guard, performance optimization, and documentation/ADRs.

## Recommended Next Improvements
- Add a command that creates branch, issue journal, and kanban card from a GitHub issue number.
- Keep kanban cards short and link them to artifact reports instead of duplicating long logs.
- Consider adding an `agent-state.json` only if machines need to consume board state; markdown is better while humans are the primary reviewers.
- If many GitHub issues are active at once, keep one branch and one `docs/issues/issue-<number>.md` journal per issue.

## Agent Flow
1. Deterministic Issue Intake and Context Compiler harness stages establish the scoped run state. Issue Intake has no LLM invocation.
2. Planner writes scope and checks; Risk Classifier sets autonomy gates.
3. Implementation Agent patches narrowly.
4. Quality Runner and Security Agent provide required quality and security gates.
5. Frontend QA runs only for user-visible changes; Architecture and Semantic checks run only for code-impacting changes.
6. Reviewer compares the diff against policy and lessons.
7. Orchestrator writes the final verdict; Publication Prepare can run only after required gates pass.

The role result field `next_action` is advisory. `scripts/workflow_router.py` is authoritative and derives the next role from policy, risk, artifacts, changed files, workflow state, budgets, and loop counters. Quality, review, CI, and frontend verification repairs use bounded loops with independent iteration, token, and time budgets. Failure and diff fingerprints prove progress; repeated failure without a changed diff stops at `approval-gate`. Each decision is schema-validated and recorded as a `router.decision` event in the run's `workflow_trace.jsonl`.

The routing contract is defined in `.agent-routing.yaml`; run state includes `loops`, `budgets`, `role_count`, and cumulative `tokens_used` so a workflow cannot silently exceed its execution limits.

Security routing is severity-aware. A `critical` finding returns a hard `blocked` terminal state and a structured `ROUTER_BLOCKED` error. `medium` and `high` findings route to human approval. `none` and `low` do not create a security stop when the verifier contract is otherwise valid.

## Concurrent Execution And Verification

- Security, code review, architecture consistency, semantic conflict, and frontend/user-flow checks are separate read-only verifiers with one shared verdict contract.
- UI verification may report `works` only with a real loopback development server, Playwright interaction evidence, screenshots, and console/network observations. `unavailable` keeps publication in draft when evidence is required; `broken` enters the bounded frontend repair loop.
- Task Intake establishes the authoritative task workspace before implementation. For the default CLI flow this is the dedicated branch already prepared in the current checkout; isolated parallel execution creates a worktree. Publication validates and reuses the workspace and branch recorded in `workflow.json`; it does not copy changes into a publication-only checkout.
- `ai_harness/recovery/` and `.agent-recovery.yaml` own sanitized failure records, bounded recovery decisions, role checkpoints, backoff, validation-only output repair, and idempotency probes.
- `scripts/task_queue.py` provides an idempotent SQLite queue with atomic leases, recovery scheduling, explicit retry/repair/resume/approval/dead-letter states, and compatible schema evolution. `scripts/worker_pool.py` runs three isolated workers by default.
- `scripts/worker_service.py` is the operational wrapper: registered slots, daemon start/restart/stop, health, graceful draining, heartbeat monitoring, task-failure isolation, and consecutive system-failure degradation.
- `.agent-queue/tasks.db` contains only scheduling state. Task workflow state remains authoritative under `.agent-runs/<run-id>/`.
- `scripts/list_runs.py` exposes human exceptions without transcripts. `.agent-tool-policy.yaml` governs tool roles/actions/domains/credentials/timeouts and writes sanitized decisions to each run's tool-call audit.
- `scripts/approval_lifecycle.py` owns scoped approval request/approve/reject/expire/consume transitions. A consumed approval queues the same `run_id`; `agent_role_runner.py --resume` reuses the recorded worktree and checkpoint.
- `scripts/event_ingestion.py` normalizes CLI, API, webhook, GitHub Issue, and CI deliveries into one idempotent task envelope.
- `scripts/ci_feedback.py` verifies GitHub webhook HMAC, reads failed logs through tool governance, redacts credential patterns, stores CI evidence inside the run, and queues `ci-repair-agent` against the existing branch and PR.
- `scripts/control_plane_api.py` is a loopback-only API for intake, approvals, resume, exceptions, and metrics. `scripts/operational_metrics.py` exposes compact runs/workers/queue/leases/budgets/exceptions state without transcripts.

## Runtime Abstraction

The Harness controls a provider-neutral runtime boundary:

```text
Harness -> Runtime Adapter -> Codex CLI
```

Every model-backed role uses `Runtime.execute(role, context, task, worktree, artifacts)`. Harness code never constructs `codex exec`, calls an OpenAI or Anthropic API, or imports provider-specific execution code. `.agent-runtime.yaml` configures the only Step 2 production provider, `codex-cli`, using a local subscription transport with `api_required: false`. Runtime identity and provenance are stored in each authoritative run.

Additional OpenAI, Claude, or Ollama adapters are deferred to Step 3. A Model Router is explicitly deferred to Step 4; deterministic workflow routing remains authoritative and is not a model-selection router.

Step 2 production acceptance is evidence-gated by `make step2-verify`. It requires real concurrent Codex CLI runtime runs, isolated worktrees, independent gates, governed tool traces, at least one PR, and at least one human exception; fixture-only concurrency is not sufficient.

## Runtime And Step 1 Gates
The configured production runtime has a provider-neutral preflight; Codex CLI also retains its explicit compatibility gate and real smoke:

```sh
make runtime-preflight
make codex-preflight
make codex-smoke
```

`make codex-preflight` aliases the configured runtime preflight. The smoke must run against a real authenticated Codex CLI. It verifies that the Planner role completes, creates `plan.md` and `project_profile.json`, preserves a clean read-only repository, and records raw JSONL plus token usage.

Step 1 closes only after a selected 10-20 run manifest passes `make step1-verify`. The verifier rejects fake/external adapter provenance, missing gates, default-branch mutations, HIGH publication, missing PRs for LOW/MEDIUM, duplicate publications, secret leakage, missing token evidence, and unstructured terminal errors.
