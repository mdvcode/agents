# Workflow Graph

## User CLI Flow

`./install.sh` -> `agent init` -> `.agent/project.yaml` + `AGENTS.md` -> clean checkout (setup committed or intentionally ignored) -> `agent task "Goal"` -> dedicated branch in current checkout + worker auto-start + normalized Task envelope -> SQLite queue -> workflow -> `agent status` -> PR or compact exception. Later, `agent update` refreshes the installed package and restarts the worker without overwriting a dirty source checkout.

`agent task --current-branch "Goal"` binds the run to an already prepared clean branch. `agent task --worktree "Goal"` explicitly selects isolated parallel execution. The queue reserves a current checkout for its unfinished branch task until completion or explicit cancellation, and execution stops if the checked-out branch changed after intake.

Project-local configuration authorizes local execution only. Central Harness policy and repository trust remain mandatory for publication.

## GitHub Issue Flow
User gives project + GitHub issue -> branch -> `docs/projects/<project>/issues/issue-<number>.md` -> one `.agent-runs/<run-id>/` -> plan -> risk -> patch -> tests -> quality -> security -> review -> verdict -> PR/handoff -> project wiki/memory update.

## Target Project Auto-To-PR Flow
User gives a registered project issue -> public-safe branch name -> private issue journal -> plan/risk -> read `.agent-policy.yaml` and `.agent-repositories.yaml` -> minimal patch -> focused checks -> local dev server when relevant -> screenshot/video/trace evidence when required -> report/verdict -> verify git identity -> exclude control-plane/private files -> stage task-scoped public files only -> commit with no agent or AI wording -> push branch -> create/update sanitized PR with no agent or AI wording -> send PR URL and local website URL when relevant -> update private issue journal and audit log.

## Artifact Flow
Within one `.agent-runs/<run-id>/`: `plan.md` -> `risk.json` -> implementation -> tests -> `quality.json` -> `security.json` -> `review.json` -> `verdict.json` -> `change_set.json` + `publication_payload.json` -> `publication.json` -> `audit-log.jsonl`.

## Concurrent Task Flow

Task enqueue -> SQLite lease -> worker heartbeat + worker-owned SDK sidecar -> Task Intake binds the prepared branch or creates an opted-in worktree -> authoritative router -> one run-bound SDK thread across implementation, bounded repair, user-answer continuation, and independent verification -> publication from the same workspace or compact exception -> terminal queue status.

Approval required -> run-scoped request and checkpoint fingerprint -> exact-scope human decision -> consume once -> queue existing run id -> resume same worktree/checkpoint -> continue deterministic gates.

Missing information -> role returns `awaiting_approval` with a concrete question and optional 2-3 structured choices -> workflow and queue preserve `ATTENTION REQUIRED` details -> dashboard/status/watch show the choices plus a custom-answer path -> `agent answer <run-id> ...` records sanitized private input -> consumes only the matching scoped continuation gate -> same run/checkpoint resumes with the answer in its role prompt -> a repeated fingerprint after an answer stops as a visible technical blocker instead of opening another question gate.

Worker process dies -> heartbeat stops -> lease expires -> task requeued with existing run id -> replacement worker detects running/resuming workflow -> `--resume` from checkpoint.

Role/runtime/tool failure -> deterministic `FailureRecord` -> `.agent-recovery.yaml` decision -> `retry_wait`, `repairing`, `resuming`, `awaiting_approval`, `dead_letter`, or terminal `failed` -> same queue task/run/worktree continues when recoverable.

Role checkpoint sequence -> `role_pending` -> `role_running` -> `role_output_received` -> `role_validating` -> `role_completed`. Resume repeats execution only from pending/running, replays cached validation from output-received/validating, and advances after completed.

GitHub Actions failure -> HMAC-verified webhook -> governed failed-log read -> secret redaction -> run-scoped CI feedback -> existing run/branch queued at CI repair -> quality and publication update the existing PR.

The queue coordinates tasks; it never replaces `.agent-runs/<run-id>/` as the authoritative state of a task.

## Runtime Flow

Harness role request -> provider-neutral `Runtime.execute(...)` -> bounded Python Codex SDK adapter -> worker-owned Unix socket -> persistent local Codex app-server with ChatGPT subscription -> one `run_id`-bound thread -> streamed SDK/tool progress + structured role result -> authoritative run state.

The worker heartbeat checks the sidecar lifecycle. SDK notifications and tool activity update `progress.json`; process idle detection watches that file and `sdk-events.jsonl`, so a quiet stdout does not look stuck while the SDK is progressing. Sidecar age/request budgets trigger recycle, and a replacement process resumes the persisted thread id.

The official `codex-sdk` is the production provider, `codex-cli` is a compatibility fallback, and Model Router is disabled. Runtime configuration defines exact Sol/high, Terra/medium, and Luna/low profiles with the Fast service tier; local deterministic policy selects one profile from role, risk, change impact, repair iteration, and actual failure state.

## Deterministic Gate Flow

HIGH risk -> approval; CRITICAL security -> blocked; MEDIUM/HIGH security -> approval; UI changed -> frontend verifier; quality/review/CI/frontend broken -> bounded repair; repeated failure plus unchanged diff -> approval; all required gates valid -> publication. Model `next_action` is advisory throughout.

Issue Intake is a deterministic harness stage (`llm_invocation=false`), not an LLM role. It records `workspace_mode`, `checkout_path`, `task_branch`, `base_sha`, and `branch_owner_run_id` before any model-backed role runs.

## Knowledge Flow
Static source -> Knowledge Source -> Retriever -> Context Builder -> Context Package -> Runtime -> role.

## Memory Retrieval Flow
Memory candidate -> future MemPalace implementation -> `MemoryManager` lifecycle -> approved memory source -> Retriever -> Context Builder. The current milestone stops at the interface and does not inject long-term memory.

## Context Intelligence Flow

Task + repository + role + runtime -> bounded repository/Obsidian/profile/policy/skill/contract sources -> deterministic rule retrieval -> prioritized token budgets -> one Context Package + provenance log -> runtime reads the package only.

## Evaluation Flow

Frozen dataset + frozen rubric + explicit subject run mapping -> deterministic scorecards + evidence coverage -> dataset gates -> paired compatible comparison -> regression verdict -> coverage-aware leaderboard.

Missing telemetry remains unavailable and reduces coverage. Dataset expectations stay outside the evaluated run context.

Production contract datasets -> specialized deterministic scorers -> current corpus report -> exact dataset/case compatibility check -> frozen baseline -> non-compensating critical metric thresholds -> CI pass or non-zero regression exit.

## Observability Flow

Queue task -> worker task span -> W3C trace-context injection -> workflow span -> step/retry/iteration spans -> sanitized run-scoped JSONL + optional OTLP/HTTP exporter.

Authoritative run artifacts + scheduler database + bounded recent spans -> operational snapshot -> authenticated JSON API -> data-free loopback dashboard shell. Telemetry delivery failure never changes workflow outcome.

## Stop Flow
Protected path or high risk -> update risk/verdict -> stop and request approval.
