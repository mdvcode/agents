# Python Codex SDK Subscription Runtime

Date: 2026-08-15

## Decision

The official `openai-codex` Python SDK is the primary production implementation behind the existing provider-neutral `Runtime` boundary. It connects to a local Codex app-server and reuses existing Codex authentication.

Production configuration is intentionally fixed:

- transport: `local_subscription`;
- required account type: `chatgpt`;
- provider API required: false;
- model: `gpt-5.6-sol`;
- reasoning effort: `high`;
- service tier: `fast`;
- Model Router: disabled.

An API-key account is rejected before a role turn. The Harness never reads, writes, copies, or logs credential material. The `codex-cli` adapter remains an explicit compatibility fallback, not a second model router.

## Reliability boundary

Each SDK role executor runs in a managed subprocess. The outer Harness retains hard wall-clock and idle timeouts, process-tree termination, output limits, artifact limits, file-descriptor limits, read-only Git snapshots, structured-output validation, and at most two validation-only repair turns. This keeps SDK integration killable even if its JSON-RPC connection or local Codex process stops making progress.

Worker state records a deterministic build fingerprint. `agent doctor` compares the running worker with the installed files and, when invoked from the Harness source repository, compares the installed copy with that checkout. A stale long-lived worker can no longer look healthy solely because its PID is alive.

## Git workspace decision

Ordinary tasks use one fresh task branch in the current checkout. Existing branch names are never silently reused for a new task, while repeat submission of the same queued task id stays idempotent. Branch creation is recorded before queue ownership and is rolled back only when the branch is still clean and unchanged after a queueing failure. Worktrees remain an explicit `--worktree` option for parallel tasks.

## Consequences

- Subscription and API billing modes cannot be confused silently.
- Sol/high/Fast prioritizes capability and latency; Fast consumes subscription credits faster than standard service.
- SDK structured output and token usage replace CLI JSONL parsing on the primary path.
- The provider-neutral workflow, deterministic gates, recovery policy, and publication authority remain unchanged.
- The earlier CLI-only ADR remains historical context and is superseded for production runtime selection by this decision.
