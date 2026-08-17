# Batch and Child Orchestration

Date: 2026-08-17  
Status: accepted

## Decision

The Harness accepts bounded task batches through CLI, loopback UI, and API. A manifest may name several initialized repositories and set `max_parallel_tasks` per repository. Visual `parallel` intent maps to an isolated worktree; the CLI retains explicit `--worktree` behavior.

Independent work discovered during execution uses a bounded parent/child task graph. The model may propose child work only through structured role output. The deterministic router validates the spawning role, relation, dependency mode, repository, path scope, fingerprint, depth, fan-out, and child budget before queueing it.

Every writing child owns a worktree, task branch, SDK thread, and scoped patch artifact. Only the parent may consume child patches, repair join conflicts, execute final combined gates, and publish. A required failing check is repaired sequentially in the same run instead of being hidden behind child concurrency.

## Invariants

- One writer owns each checkout and branch.
- Repository concurrency is checked atomically at lease claim time.
- A parent has at most three children; graph depth is at most two.
- Child changes outside `allowed_paths` are blocked.
- Duplicate spawn fingerprints do not create new work.
- The children consume bounded token/time budgets and cannot publish.
- Cross-repository parallel work is explicit batch intake; automatic writing children stay in the parent repository.
- Every joined result is followed by the parent's complete verification path.

## Operational consequences

Dependency downloads use shared private pip, uv, npm, and Bun caches. Repository-stable cache roots are also exposed for build outputs, virtual environments, and container layers, while checkouts remain isolated.

The dashboard normalizes work into `Queued`, `Running`, `Testing`, `Needs input`, `PR ready`, and `Failed`. It filters across repository, branch, and worker, and reports probable branch conflicts from overlapping active changed paths with a deterministic publish-first/rebase-second order.

The queue remains scheduler state only. Parent/child state and join decisions remain authoritative in each run's `workflow.json` and run-scoped artifacts.
