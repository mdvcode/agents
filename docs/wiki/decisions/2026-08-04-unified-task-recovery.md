# Decision: Unified Task-Level Recovery

## Status

Accepted on 2026-08-04. Production recovery evidence remains acceptance-gated.

## Context

The queue already reclaimed expired leases and the workflow already had bounded verification repair loops, but failures around a role or subprocess were flattened into `blocked` or terminal `failed`. That made a task-level runtime fault indistinguishable from a service fault and forced manual task recreation.

## Decision

- `.agent-recovery.yaml` is the Harness-owned recovery policy. Model output can describe a failure but cannot choose retry, repair, resume, approval, or terminal routing.
- Every handled failure is a sanitized `FailureRecord` under `.agent-runs/<run-id>/failures/` and is referenced from `workflow.json`, queue coordination fields, and `errors.jsonl`.
- Retry repeats the current operation, output repair receives only the original structured output, schema, and validation errors, verification failures continue to use the existing repair loops, and resume reads the last atomic role checkpoint.
- Recovery preserves the authoritative `run_id`, repository, task worktree, branch, and artifact directory. A corrupted or mismatched checkpoint goes to controlled dead letter rather than guessing.
- The queue exposes `retry_wait`, `repairing`, `resuming`, `awaiting_approval`, terminal `failed`, `dead_letter`, and `cancelled` while retaining compatibility with existing leased tasks and databases.
- Worker task execution, thread futures, telemetry shutdown, heartbeat updates, and the long-lived service loop have independent error boundaries. Only consecutive system-level wave failures degrade service health; ordinary task failures do not increment the service restart budget.
- Workflow subprocess exit codes distinguish approval, retryable, repairable, resumable, dead-letter, unrecoverable, and invalid Harness state outcomes.
- Publication writes the run-scoped idempotency key into the commit trailer. Before any repeated side effect it reconciles the marker, the exact remote branch SHA, and the existing PR, then atomically checkpoints recovered state.
- Approval request, decision, grant consumption, and continuation enqueue are serialized by a run-scoped file lock. The workflow grant is durable before the approval becomes consumed; replay returns the same grant and queue task without applying either twice.
- Schema validation and owned-artifact validation share one two-attempt, read-only output-repair budget. Exhaustion routes directly to dead letter instead of rerunning the ordinary role prompt.

## Safety bounds

Recovery has total-attempt, class-attempt, resume-attempt, consecutive-failure, duration, token, output-repair, subprocess-concurrency, open-file, output-byte, and artifact-byte limits. Persistent worker-pool restart recovery also has attempt, elapsed-time, backoff, and terminal stop bounds. Observability is fail-open and excludes prompts, source contents, credentials, authorization headers, and raw secrets.

## Consequences

A timeout or temporary runtime failure no longer makes the task terminal. Another worker can continue the same run and worktree from a checkpoint. Exhausted or unsafe recovery remains visible as `dead_letter` or terminal `failed`, and humans can inspect or explicitly retry/resume/abort it through the CLI.

## Production runtime extension — 2026-08-05

Workflow, role-adapter, primary Codex, and output-repair processes now use bounded file-backed stdout/stderr, distinct process sessions, recursive descendant termination, SIGTERM-to-SIGKILL escalation, active cancellation flags, workflow/idle/output/artifact/open-file limits, and graceful service interruption that requeues the same run. SQLite write contention uses finite backoff; expired-lease disk/telemetry work happens only after transaction commit; checkpoints and failure records are fsync-backed. Queue records expose an explicit `lease_owner` while preserving the compatible `worker_id` field. Publication reconciliation checks commit markers, exact remote heads, head branches, run markers, and idempotency keys and refuses changed inputs after an irreversible step. Readiness remains gated by both the deterministic chaos suite and a real 30-task, multi-hour soak report with service, identity, lease, commit, and PR invariants; unit tests alone cannot close that operational gate.
