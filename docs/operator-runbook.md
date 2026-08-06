# AI Harness Operator Runbook

This is the shortest supported path from a repository to autonomous execution.

## First start

Download and unpack the system, then install it from that folder:

```sh
cd /path/to/downloaded-agents
./install.sh
```

For future updates, run `agent update`; direct pipx commands are not part of the ordinary user flow.

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

`agent task` prepares a dedicated branch in the current checkout, starts autonomous workers when necessary, and queues the work. The checkout must be clean before it switches branches. The service stays available for all locally initialized projects until `agent stop` is called. `agent start` remains an optional proactive service command.

Generated branch prefixes are configurable and are not limited to a fixed allowlist:

```sh
agent init --force --branch-prefix team/backend/
```

Use `agent task --worktree ...` only when isolated parallel task directories are intentional. Without that flag, a second task for the same repository is refused until the first task is completed or cancelled.

If the task needs a fact or choice, `agent watch` exits with an `ATTENTION REQUIRED` block. Answer its exact run without starting over:

```sh
agent answer <run-id> "Use the existing branch and keep backward compatibility"
agent watch --run-id <run-id>
```

Use `agent approve` only for the explicit risk/security/publication decision printed by status. `agent answer` is deliberately unable to replace such approval. Never put credentials or private customer data in an answer.

## What the states mean

- `queued`: the task is waiting. `agent task` normally starts the worker; if it later stops, use `agent start`.
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
- `agent update` is an invalid command: the installed CLI predates self-update. Run the current `install.sh` once from a fresh download (or use the documented `curl ... | sh` installer), open a new terminal or run `hash -r`, then use `agent update` normally.
- Missing or wrong base branch: fetch it, or run `agent init --force --base-branch <existing-branch>`.
- Worker not running: `agent start`.
- Worker unhealthy after an upgrade: `agent worker restart`, then inspect the log path printed by the command.
- Pending approval: read the reason in `agent status`, then run its exact `agent approve` command if acceptable.
- Missing information: use the exact `agent answer` command printed under `ATTENTION REQUIRED`, then continue with `agent watch --run-id ...`.
- Stuck or failed run after the underlying cause is fixed: `agent retry <run-id>`; use `agent resume <run-id>` only for a recorded resume checkpoint.

Stop the service gracefully with:

```sh
agent stop
```

Do not edit queue rows or workflow status files by hand. Approval, retry, resume, abort, and worker commands preserve the authoritative run identity and recovery checkpoint.
