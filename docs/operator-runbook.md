# AI Harness Operator Runbook

This is the shortest supported path from a repository to autonomous execution.

## First start

For the local, unpublished Tweebit v0.3.0 release candidate, use the exact reviewed checkout rather
than the public `mdvcode/agents` installer, which installs the baseline:

```sh
cd /absolute/path/to/reviewed/tweebit-checkout
./install.sh
```

Keep updates pinned with `agent update --source /absolute/path/to/reviewed/tweebit-checkout` until
Tweebit is published; direct pipx commands are not part of the ordinary user flow.

Initialize the target project and validate the complete runtime:

```sh
cd /path/to/project
agent init
agent doctor --full
```

If `agent init` reports that `.agent/project.yaml` or `AGENTS.md` is ignored by Git, keep it local and do not use `git add -f`. Otherwise, commit the new setup files or add repository-approved ignore rules before starting a task. The only execution requirement is a clean checkout.

If the project must start tasks from a branch other than the detected default, make that choice explicit. This replaces only `.agent/project.yaml`; it preserves an existing `AGENTS.md`:

```sh
agent init --force --base-branch develop
agent doctor --full
```

Queue work and watch it:

```sh
agent task --task-id fix-startup "Fix startup and add a regression test"
agent watch --task-id fix-startup
```

`agent task` checks that a known local Harness source matches the installed build, prepares a dedicated branch in the current checkout, queues the work, and verifies worker readiness. The checkout must be clean before it switches branches. A stale or unhealthy live worker is restarted automatically. If worker startup fails after queueing, the error includes the preserved run id and exact restart/watch commands; do not submit a replacement task. The service stays available for all locally initialized projects until `agent stop` is called. `agent start` remains an optional proactive service command.

Generated branch prefixes are configurable and are not limited to a fixed allowlist:

```sh
agent init --force --branch-prefix team/backend/
```

Use `agent task --worktree ...` only when isolated parallel task directories are intentional. Without that flag, a second task for the same repository is refused until the first task is completed or cancelled.

If the checkout owner is already waiting for human input, blocked, dead-lettered, or failed, submitting a new ordinary `agent task` replaces that paused run automatically while preserving its branch and run files. Running and queued tasks are never replaced. Use `--keep-paused` only when retaining the paused run is more important than accepting the new task.

If the task needs a fact or choice, `agent watch` exits with an `ATTENTION REQUIRED` block. Answer its exact run without starting over:

```sh
agent answer <run-id> "Use the existing branch and keep backward compatibility"
agent watch --run-id <run-id>
```

Use `agent approve` only for the explicit risk/security/publication decision printed by status. `agent answer` is deliberately unable to replace such approval. Never put credentials or private customer data in an answer.

## Execution bounds

- One model-backed role executor has a 30-minute emergency limit. This is not the task duration. The full process tree is terminated when the limit, cancellation, output limit, or idle limit is reached.
- Fast mode has a 15-minute workflow limit. Ordinary `full` mode has a 60-minute workflow limit. Multi-hour execution is never auto-selected: explicit `goal` mode has a 4-hour limit and uses the full specialist route with checkpoints. The outer worker stops a workflow subprocess that produces no output for 35 minutes; an individual model executor remains subject to the shorter 30-minute role cap and the remaining workflow budget.
- Role/iteration limits, total workflow time, automatic-recovery time, token budgets, and human-attention waits are independent controls. Token and model-call ceilings are soft cost controls: at or above a ceiling the harness selects economy profiles for mandatory work and may shed optional roles, but it does not pause for human approval. Provider context-window errors remain technical failures and use the normal bounded recovery path. The worker's 4-hour outer limit is the `goal` cap and a failsafe for ordinary modes, whose shorter limits apply first.
- Automatic recovery has at most eight total recovery decisions, three checkpoint resumes, five consecutive failures, and 30 minutes from the first recorded failure. A resumed process receives only the remaining recovery time.
- `agent watch` returns after 30 minutes by default without stopping the task. `--timeout 0` is the explicit indefinite terminal-wait mode.
- A queued task is retained indefinitely when no worker is available so work is not discarded. This is not treated as active execution: `agent watch` returns immediately with `agent start`, and `agent status` shows the queue age.
- Human approval expires after 24 hours. Approval is an intentional wait, not a running process; expiry moves both workflow and queue to `blocked`, and a corrected task can then be retried.

These are wall-clock safety limits, not performance targets. A task that repeatedly reaches a limit should be inspected with `agent status` and `agent failures`, not restarted blindly.

## What the states mean

- `queued`: the task is waiting and its age is shown. `agent task` normally starts the worker; if it later stops, `agent watch` returns immediately and tells you to use `agent start`.
- `running`: a worker owns the task. Use `agent status`; do not enqueue a duplicate.
- `retry_wait`, `repairing`, or `resuming`: automatic recovery is active. The status output shows the cause, attempt count, checkpoint, and next retry time.
- `awaiting_approval`: a human decision is required. `agent status` prints the exact scoped `agent approve --run-id ...` command and the reason. Approve only when that reason and scope are acceptable.
- `awaiting_approval` with an answer command: execution needs missing information. Use the printed `agent answer` command; the same run resumes from its checkpoint.
- `blocked`: the workflow cannot proceed automatically. Read `agent failures --run-id <run-id>` and fix the reported prerequisite; approval is not a substitute unless a pending approval is explicitly shown.
- `dead_letter`: the bounded recovery budget is exhausted. Inspect `agent failures`, correct the cause, then use `agent retry <run-id>` only when retry is appropriate.
- `completed`: required local gates passed and the workflow reached its terminal result. Merge and deployment still require explicit human action.

## Diagnose a failure

Run these in order:

```sh
agent doctor --full
agent status
agent failures
agent dead-letters
agent worker status
```

The first failing doctor check is the prerequisite to repair. Common actions are:

- Missing Python runtime module: `agent update`, then `agent doctor --full`.
- Installed build differs from its local source checkout: run the exact `agent update --source <path>` command reported by `agent doctor`, then retry the same task.
- `agent update` is an invalid command: the installed CLI predates self-update. Bootstrap with
  `install.sh` from the reviewed local Tweebit checkout, open a new terminal or run `hash -r`, then
  keep this candidate pinned with `agent update --source
  /absolute/path/to/reviewed/tweebit-checkout`. The public `curl ... | sh` installer provides only
  the public baseline.
- Missing or wrong base branch: fetch it, or run `agent init --force --base-branch <existing-branch>`.
- Worker not running: `agent start`.
- Worker unhealthy after an upgrade: `agent worker restart`, then inspect the log path printed by the command.
- Worker failed after a run was queued: run `agent worker restart`, then use the printed `agent watch --run-id <run-id>` command; the queued run is retained.
- Pending approval: read the reason in `agent status`, then run its exact `agent approve` command if acceptable.
- Missing information: use the exact `agent answer` command printed under `ATTENTION REQUIRED`, then continue with `agent watch --run-id ...`.
- Stuck or failed run after the underlying cause is fixed: `agent retry <run-id>`; use `agent resume <run-id>` only for a recorded resume checkpoint.

Stop the service gracefully with:

```sh
agent stop
```

Do not edit queue rows or workflow status files by hand. Approval, retry, resume, abort, and worker commands preserve the authoritative run identity and recovery checkpoint.

## Which files to keep

The Harness has three distinct file classes. Treating them as one is a common source of broken runs and accidental cleanup.

### Required source and policy

Keep these files in the Harness repository and under version control:

- `ai_harness/`, `scripts/`, `schemas/`, `.agents/`, and `tests/`
- `AGENTS.md`, `Makefile`, `pyproject.toml`, `install.sh`, and `requirements-dev.txt`
- `.agent-*.yaml` policy, routing, workflow, profile, ownership, runtime, and registry files
- `docs/` and `evals/` when they contain reviewed policy, operating guidance, memory, or acceptance data

In a target project, `.agent/project.yaml` is local execution identity and `AGENTS.md` is target-specific guidance. They may be intentionally ignored by Git. A target project does not need copies of Harness-only paths such as `docs/memory/lessons_learned.md`, `.agent-runs/`, or the Harness policy YAML files.

### Generated operational state

These directories are required while their tasks are active or may need recovery, but must not be committed:

- `.agent-queue/`: scheduler database, leases, retries, worker logs, and managed SDK session state
- `.agent-runs/`: workflow state, checkpoints, evidence, errors, and audit records
- `.agent-worktrees/`: isolated task checkouts
- `.agent-cache/`: reusable task caches

The installed Harness owns the production queue and run state. Same-named directories in a source checkout are development/test state unless that checkout is itself the active Harness home. Do not delete active run or queue data manually. Finish or abort tasks first, stop the worker, confirm `agent status`, and use supported cleanup commands when available.

### Re-creatable and disposable

These files are not authoritative and may be regenerated after confirming no process uses them:

- `.venv/`, `build/`, `dist/`, and `*.egg-info/`
- `__pycache__/`, `*.pyc`, `.pytest_cache/`, and tool caches
- `tmp/` and `output/` investigation or generated outputs after useful evidence is summarized
- `.DS_Store` and editor-local state such as `.idea/`

Deleting these saves space but does not repair workflow state. Never treat `.agent-runs/`, `.agent-queue/`, or `.agent-worktrees/` as ordinary caches merely because they are ignored by Git.
