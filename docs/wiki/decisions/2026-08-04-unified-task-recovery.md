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
- Publication stores a run-scoped idempotency key and continues its existing commit/push/PR resume checks. Approval grants remain one-shot.

## Safety bounds

Recovery has total-attempt, class-attempt, resume-attempt, consecutive-failure, duration, and output-repair limits. Observability is fail-open and excludes prompts, source contents, credentials, authorization headers, and raw secrets.

## Consequences

A timeout or temporary runtime failure no longer makes the task terminal. Another worker can continue the same run and worktree from a checkpoint. Exhausted or unsafe recovery remains visible as `dead_letter` or terminal `failed`, and humans can inspect or explicitly retry/resume/abort it through the CLI.
