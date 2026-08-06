# Agent Workspace Index

This repository is the local operating base for agents working across supported project profiles.

## Start Here
- `docs/onboarding.md`: how a new agent should enter the workspace.
- `docs/agent-system.md`: current agent analysis and improvement plan.
- `docs/evaluation-framework.md`: versioned run scoring, 30-case production corpus, frozen comparisons, CI regression gate, coverage, and leaderboard workflow.
- `docs/observability.md`: OpenTelemetry traces and metrics, operational snapshot, privacy boundaries, and loopback dashboard.
- `docs/cli.md`: install and use the `agent` product CLI from any project.
- `docs/operator-runbook.md`: one copyable start path plus failure, approval, and recovery actions.
- `docs/git-and-logs.md`: git, logs, docs, and artifact expectations.
- `docs/issues/README.md`: per-GitHub-issue history and branch tracking.
- `docs/projects/README.md`: multi-project private memory layout and privacy rules.
- `docs/wiki/index.md`: curated compounding knowledge.
- `docs/memory/MEMORY.md`: cross-issue long-term memory.
- `docs/graph/README.md`: project maps for files, workflows, risks, and agents.
- `docs/templates/goal.md`: `/goal` structure for non-trivial tasks.

## Kanban Boards
- `docs/kanban/tasks.md`: active tasks and process state.
- `docs/kanban/tests-and-fixes.md`: test failures, fixes, and retest loops.
- `docs/kanban/features.md`: feature ideas and delivery slices.

## Issue History
- `docs/projects/<project>/issues/issue-<number>.md`: durable private timeline for one GitHub issue and its branch.
- `docs/issues/_template.md`: copy this when starting a new project issue journal.

## Projects
- `docs/projects/_template/`: copy this for each new project.
- `docs/projects/<project>/privacy.md`: what can and cannot be published.
- `docs/projects/<project>/issues/`: private issue journals.
- `docs/projects/<project>/memory/`: project-private long-term memory.
- `docs/projects/<project>/wiki/`: project-private curated knowledge.
- `docs/projects/<project>/graph/`: project-private maps.

## Memory And Knowledge
- `docs/wiki/concepts/context-intelligence-platform.md`: Context Engine, Knowledge Sources, Retriever, Context Builder, budgets, logs, and MemPalace contract.
- `docs/wiki/`: stable cross-project agent-system knowledge.
- `docs/memory/`: cross-project agent-system memory, daily logs, scratchpad, and topics.
- `docs/graph/`: cross-project agent-system maps that help agents navigate without broad scans.

## Validation
- `make check`: validate artifacts and diff hygiene.
- `make validate-artifacts`: validate required structured artifacts.
- `make runtime-preflight`: preflight the production provider through the Runtime abstraction (`codex-cli` in Step 2).
- `make security`: lightweight repository-local scan for obvious secrets, private keys, private paths, and protected staged files.
- `make agent-status`: print git status and current verdict.
- `make queue-worker`: process queued workflows with three leased workers.
- `make worker-service-start|restart|status|health|stop`: operate the registered worker daemon.
- `make runtime-chaos`: run the bounded production-runtime failure-injection suite.
- `make runtime-soak SOAK_MANIFEST=<manifest> SOAK_REPORT=<report>`: collect the real 30-task, multi-hour acceptance run; `make runtime-soak-verify` validates its report.
- `make approve-run|resume-run|reject-run RUN_ID=...`: perform scoped supervised-autonomy transitions.
- `make control-plane` or `make dashboard`: serve the loopback control API and dashboard; `make metrics` prints the same compact operational state.
- `make list-exceptions`: show runs and queue items requiring a human without transcripts.
- `make step2-verify RUN_ID=<run-id> QUEUE_DB=<path>`: verify real concurrent Step 2 acceptance evidence.
- `make eval-score EVAL_RUN_DIR=<path> EVAL_OUTPUT=<path>`: score one authoritative Harness run.
- `make eval-regression`: evaluate the frozen 30-case production corpus and fail on critical or incompatible regressions.

## Product CLI

- `./install.sh`: install the downloaded system and expose the `agent` command without requiring pipx knowledge or `sudo`.
- `agent update`: safely download and install updates, verify the command, and restart the worker service.
- `agent init`: create `.agent/project.yaml` and safe project instructions.
- `agent task "Goal"`: create a dedicated task branch in the current checkout, start workers when needed, and queue autonomous work.
- `agent start`: optionally validate the current project and start the autonomous worker service in advance.
- `agent stop`: stop the autonomous worker service gracefully.
- `agent task --current-branch "Goal"`: use the clean, already checked-out non-default branch.
- `agent task --worktree "Goal"`: explicitly opt into an isolated worktree for parallel work.
- `agent status`: show compact project queue/run state.
- `agent watch`: follow task transitions until completion or a required user action.
- `agent answer <run-id> "Answer"`: provide requested information and resume the same scoped run.
- `agent doctor [--full]`: validate packaging, project config, Codex CLI, and optionally runtime authentication.

## Runtime State
- `.agent-runtime.yaml`: the single production runtime selection; Step 2 permits only local-subscription `codex-cli`, requires no API, and disables Model Router.
- `scripts/runtimes/`: provider-neutral contract, registry, generic structured subprocess boundary, and provider adapters.
- All model-backed execution must pass through `Runtime.execute(...)`. Additional adapters belong to Step 3; Model Router belongs to Step 4.
- `.agent-runs/<run-id>/workflow.json`: authoritative workflow state.
- `.agent-runs/<run-id>/context-manifests/`: scoped role context.
- `.agent-runs/<run-id>/role-results/`: role checkpoints.
- `.agent-runs/<run-id>/raw-events/`: raw executor JSONL and usage evidence.
- `.agent-runs/<run-id>/raw-events/otel-spans.jsonl`: bounded, sanitized OpenTelemetry span evidence.
- `.agent-runs/<run-id>/artifacts/`: owned plan, risk, quality, security, review, verdict, and publication outputs.
- `.agent-runs/<run-id>/metrics.json`: per-role duration and token usage.
- `.agent-runs/<run-id>/errors.jsonl`: structured failures and approval stops.
- `.agent-runs/<run-id>/raw-events/workflow.stdout.log` and `workflow.stderr.log`: bounded file-backed workflow output used to avoid unread-pipe deadlocks.
- Repository-root `artifacts/` is forbidden mutable state.
- `.agent-queue/tasks.db` is scheduler coordination state only; it does not mirror run artifacts or workflow state.
- `.agent-queue/events/` stores normalized immutable intake envelopes; worker-service state is operational scheduler metadata, never task workflow state.
