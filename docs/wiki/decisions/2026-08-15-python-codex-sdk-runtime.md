# Python Codex SDK Subscription Runtime

Date: 2026-08-15

## Decision

The official `openai-codex` Python SDK is the primary production implementation behind the existing provider-neutral `Runtime` boundary. It connects to a local Codex app-server and reuses existing Codex authentication.

The initial production configuration was intentionally fixed:

- transport: `local_subscription`;
- required account type: `chatgpt`;
- provider API required: false;
- model: `gpt-5.6-sol`;
- reasoning effort: `high`;
- service tier: `fast`;
- Model Router: disabled.

The fixed model portion of this decision is superseded by
[`2026-08-17-deterministic-model-and-workflow-policy.md`](2026-08-17-deterministic-model-and-workflow-policy.md).
The provider, authentication, persistent app-server, and run-bound thread decisions remain active.

An API-key account is rejected before a role turn. The Harness never reads, writes, copies, or logs credential material. The `codex-cli` adapter remains an explicit compatibility fallback, not a second model router.

## Reliability boundary

Each queue worker owns one managed SDK sidecar and therefore one Codex app-server process. All model-backed roles for a run use one persisted thread id; implementation, repair, a user-answer continuation, and verification resume that thread. The sidecar is heartbeat-checked, recycles after an age or request budget, restarts after failure, and resumes persisted non-ephemeral threads after process replacement.

Each role adapter remains a bounded subprocess. It talks to the sidecar through a worker-owned `0600` Unix socket, and the sidecar interrupts the active SDK turn when that bounded client disconnects. The outer Harness retains hard wall-clock and event-aware idle timeouts, process-tree termination, output limits, artifact limits, file-descriptor limits, read-only Git snapshots, structured-output validation, and at most two validation-only repair turns.

SDK notifications are appended to run-scoped evidence and atomically update `progress.json`. Live status reports the current phase, latest SDK event, active tool, seconds since progress, token budget usage, and stop reason. Idle detection treats SDK notifications and tool activity as progress; stdout alone is not a liveness signal.

Worker state records a deterministic build fingerprint. `agent doctor` compares the running worker with the installed files and, when invoked from the Harness source repository, compares the installed copy with that checkout. A stale long-lived worker can no longer look healthy solely because its PID is alive.

## Git workspace decision

Ordinary tasks use one fresh task branch in the current checkout. Existing branch names are never silently reused for a new task, while repeat submission of the same queued task id stays idempotent. Branch creation is recorded before queue ownership and is rolled back only when the branch is still clean and unchanged after a queueing failure. Worktrees remain an explicit `--worktree` option for parallel tasks.

New task and workflow state uses one Git identity contract: `workspace_mode` (`checkout` or `worktree`), `checkout_path`, `task_branch`, `base_sha`, and `branch_owner_run_id`. Readers accept the historic `current_branch` and `isolated` values for recovery compatibility, but new intake never emits them. Workflow configuration does not imply a worktree; `run_workflow.py` selects `--current-branch` or `--worktree` from the explicit mode. The old `--create-worktree` runner flag remains a hidden recovery alias only.

## Consequences

- Subscription and API billing modes cannot be confused silently.
- Model selection is now governed by the deterministic execution-profile decision; Fast remains the configured service tier.
- SDK structured output and token usage replace CLI JSONL parsing on the primary path.
- Repeated role turns avoid app-server startup and preserve task context instead of repeatedly asking for it.
- An alive worker PID without a healthy SDK session or recent SDK/tool progress is not considered sufficient liveness evidence.
- The provider-neutral workflow, deterministic gates, recovery policy, and publication authority remain unchanged.
- The earlier CLI-only ADR remains historical context and is superseded for production runtime selection by this decision.
