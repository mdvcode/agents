# Step 2 Operational Control Plane

Date: 2026-07-18

## Decision

Step 2 uses one supervised operational control plane around the authoritative run state.

- Approval is a run-scoped artifact owned by `approval-gate`. The requested and approved scopes must match exactly, expire automatically, bind to a checkpoint fingerprint, and may be consumed once. Resume queues the existing run and reuses its worktree.
- Queue lease recovery preserves `run_id`. An expired active lease records `lease_expired_resume`; the replacement worker invokes the workflow with `--resume` and never creates a second run state.
- `worker_service.py` owns process lifecycle and worker registration. It supports foreground service, detached start, restart, status, database-backed health, graceful shutdown, heartbeats, stale monitoring, and a bounded restart limit.
- CLI, GitHub Issue, generic webhook, API, and CI deliveries normalize into the same idempotent task envelope before entering the queue.
- GitHub Actions feedback is accepted only for an HMAC-authenticated completed failure. Failed logs are fetched through the declared GitHub tool policy, sanitized, capped, and stored under the existing run. Repair resumes at `ci-repair-agent` on the same branch so publication updates the existing PR.
- The loopback control API exposes task intake, approve, reject, resume, health, runs, workers, queue, leases, budgets, and exceptions. It returns compact structured state and never raw transcripts. Optional bearer authentication is supported; non-loopback binding is rejected.

## State boundaries

`.agent-runs/<run-id>/` remains the only mutable workflow authority. SQLite and `.agent-queue/` contain scheduler coordination, worker registration, and immutable normalized event envelopes only. Neither may copy role artifacts or replace `workflow.json`.

## Safety consequences

- Approval cannot authorize merge, deployment, another gate, or broader paths by implication.
- Resume preserves a completed role checkpoint and applies its one-time approval to deterministic routing from that result; unfinished execution and validation checkpoints retain their existing recovery behavior.
- A hard execution-bound approval opens a fresh bounded window only for the exhausted dimensions. Risk, security, protected-path, verification, and publication gates remain independent.
- Elapsed-time ceilings stop at equality. Repair and model-escalation budgets are inclusive allowances: the configured count may execute and exhaustion begins only when a new count exceeds it.
- A terminal `completed` queue record remains immutable unless the same run has a blocked authoritative verdict, a completed blocking verifier checkpoint, and no publication side effects. Evidence-bound reconciliation resumes that verifier checkpoint and invalidates only downstream false-success history.
- Structured verifier blockers take precedence over environmental keyword heuristics, and an orchestrator verdict with failed checks or blockers cannot route to publication.
- A second blocker after resume creates a new approval request; the consumed decision is not reusable.
- Informational questions, authority approvals, and technical retry stops have separate actions. Duplicate-question suppression applies only to valid structured questions; manual recovery archives the active technical attention without discarding unrelated blockers.
- CI delivery without a valid signature or configured secret is rejected before logs or queue state are touched.
- Worker recovery continues from recorded state but still passes all deterministic gates and budgets.
- Engineering completion does not replace production acceptance evidence required by `make step2-verify`.
