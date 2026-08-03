# Workflow Graph

## User CLI Flow

`pipx install ai-harness` -> `agent init` -> `.agent/project.yaml` + `AGENTS.md` -> `agent task "Goal"` -> normalized Task envelope -> SQLite queue -> worker/worktree/workflow -> `agent status` -> PR or compact exception.

Project-local configuration authorizes onboarding into isolated execution only. Central Harness policy and repository trust remain mandatory for publication.

## GitHub Issue Flow
User gives project + GitHub issue -> branch -> `docs/projects/<project>/issues/issue-<number>.md` -> one `.agent-runs/<run-id>/` -> plan -> risk -> patch -> tests -> quality -> security -> review -> verdict -> PR/handoff -> project wiki/memory update.

## Target Project Auto-To-PR Flow
User gives a registered project issue -> public-safe branch name -> private issue journal -> plan/risk -> read `.agent-policy.yaml` and `.agent-repositories.yaml` -> minimal patch -> focused checks -> local dev server when relevant -> screenshot/video/trace evidence when required -> report/verdict -> verify git identity -> exclude control-plane/private files -> stage task-scoped public files only -> commit with no agent or AI wording -> push branch -> create/update sanitized PR with no agent or AI wording -> send PR URL and local website URL when relevant -> update private issue journal and audit log.

## Artifact Flow
Within one `.agent-runs/<run-id>/`: `plan.md` -> `risk.json` -> implementation -> tests -> `quality.json` -> `security.json` -> `review.json` -> `verdict.json` -> `change_set.json` + `publication_payload.json` -> `publication.json` -> `audit-log.jsonl`.

## Concurrent Task Flow

Task enqueue -> SQLite lease -> worker heartbeat -> Task Intake creates worktree -> authoritative router -> implementation and bounded repair loops -> independent verification plane -> publication from the same worktree or compact exception -> terminal queue status.

Approval required -> run-scoped request and checkpoint fingerprint -> exact-scope human decision -> consume once -> queue existing run id -> resume same worktree/checkpoint -> continue deterministic gates.

Worker process dies -> heartbeat stops -> lease expires -> task requeued with existing run id -> replacement worker detects running/resuming workflow -> `--resume` from checkpoint.

GitHub Actions failure -> HMAC-verified webhook -> governed failed-log read -> secret redaction -> run-scoped CI feedback -> existing run/branch queued at CI repair -> quality and publication update the existing PR.

The queue coordinates tasks; it never replaces `.agent-runs/<run-id>/` as the authoritative state of a task.

## Runtime Flow

Harness role request -> provider-neutral `Runtime.execute(...)` -> configured Runtime Adapter -> Codex CLI local subscription -> structured role result + provider trace -> authoritative run state.

Step 2 has one production provider (`codex-cli`) and no Model Router. Future provider adapters are isolated Step 3 additions behind the same contract; model selection is deferred to Step 4.

## Deterministic Gate Flow

HIGH risk -> approval; CRITICAL security -> blocked; MEDIUM/HIGH security -> approval; UI changed -> frontend verifier; quality/review/CI/frontend broken -> bounded repair; repeated failure plus unchanged diff -> approval; all required gates valid -> publication. Model `next_action` is advisory throughout.

Issue Intake is a deterministic harness stage (`llm_invocation=false`), not an LLM role. It records task/worktree identity before any model-backed role runs.

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
