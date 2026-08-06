# AI Harness CLI

The Harness is installable as the `ai-harness` Python distribution and exposes one command: `agent`.

## Install

For an ordinary installation, download and unpack the repository, then run the installer from that folder:

```sh
./install.sh
```

The installer checks Python 3.11+, installs pipx when necessary without `sudo`, installs the isolated application, verifies `agent`, and prints the first-use commands. It also works as a remote bootstrap:

```sh
curl -fsSL https://raw.githubusercontent.com/mdvcode/agents/main/install.sh | sh
```

Update an existing installation with the product command:

```sh
agent update
agent doctor --full
```

`agent update` uses the installed package source. A clean Git checkout is updated with a fast-forward-only pull; a ZIP/folder installation moves to the official repository source; a remote package installation is upgraded in place. It then verifies the new `agent` command and restarts the background worker. A dirty source checkout is never overwritten. To install a separately downloaded build explicitly, use `agent update --source /path/to/new-folder`.

Contributors may still use `pip install -e .` or direct pipx commands, but users do not need to manage those environments themselves.

The installation bundles the policy, workflow, schemas, prompts, scripts, and sole Step 2 Codex CLI runtime under an isolated environment. `AI_HARNESS_HOME` may point at a source checkout when developing or diagnosing a custom installation.

## Initialize a project

```sh
cd MyProject
agent init
```

This creates:

```text
MyProject/
  .agent/
    project.yaml
  AGENTS.md
```

Existing files are preserved. Re-running `agent init` trusts the existing project configuration instead of silently replacing it. `--force` replaces `.agent/project.yaml` but still preserves an existing `AGENTS.md`; add `--replace-agents` only when replacing that instruction file is intentional. Profile and base-branch detection recognize this agent workspace, Django projects, Next.js/web projects, remote default branches, and common local default branches. Either can also be selected explicitly.

`.agent/project.yaml` is local execution identity, not publication authority. It may select the project profile, base branch, task branch prefix, and `codex-cli` runtime. It cannot authorize push, PR publication, merge, deployment, credentials, protected paths, or a different model provider.

`agent init` also records the absolute repository path and configuration fingerprint in the current user's private Harness config. A copied or merely committed `.agent/project.yaml` is not execution authority; after moving or editing the project config, run `agent init` again.

## Create a task

Validate the installation once after installation or an upgrade:

```sh
agent doctor --full
```

```sh
agent task "Fix login"
agent task --repo . --task-id fix-login "Fix login"
agent task --current-branch --task-id fix-login "Fix login"
agent task --worktree --task-id parallel-fix "Run this task in parallel"
agent watch --task-id fix-login
```

`agent task` is the ordinary start command: it validates the request, starts the persistent worker service when necessary, prepares the task workspace, and idempotently enqueues the normalized Task envelope. Repeating the same explicit `--task-id` returns the existing queue item.

By default, the command creates or selects a dedicated task branch in the current checkout from the configured base branch. It does not create a worktree. The checkout must be clean; commit the files created by `agent init` and any other intended changes, or stash them, before starting the first task. Only one unfinished current-checkout task may own a repository at a time. This prevents concurrent workers from switching the same directory between branches.

Generated branches use the prefix selected during initialization and never use the default branch. The prefix is not restricted to a fixed list: any safe Git prefix is accepted, including `feat/`, `fix/`, `chore/`, `release/2026/`, or `team/mobile/`:

```sh
agent init --force --branch-prefix chore/
```

`--current-branch` uses an already checked-out clean non-default branch without creating or renaming it. `--worktree` is the explicit opt-in for isolated parallel task execution. The worker revalidates current-checkout branches immediately before execution. Status, retry, resume, and abort preserve the same authoritative run and workspace.

Use `--dry-run --json` to inspect the envelope without switching branches, starting workers, or changing queue state. `agent start` remains available for proactively starting the service, but it is no longer required before `agent task`.

`agent watch` follows the queue and current role until the task completes or needs attention. It prints only state transitions, so a long-running role remains identifiable without dumping transcripts. `--timeout <seconds>` returns control while leaving the task running; zero waits indefinitely.

## Inspect status

```sh
agent status
agent status --json
```

Status is read-only, project-scoped, and compact. It shows queue, run, and worker-service states without role transcripts, source contents, credentials, or raw events. Worker startup is an intentional side effect of `agent task`; read-only commands never start it.

When execution cannot continue autonomously, status and watch print an `ATTENTION REQUIRED` block with the exact question or cause, current role, and next command. If information is missing, answer it without creating a new run:

```sh
agent answer <run-id> "Use the staging API and JSON output"
```

The answer is sanitized, stored only in the private run directory, made available to the resumed role, and resumes the same checkpoint. Do not include passwords, tokens, or customer secrets. A risk, security, protected-path, or publication decision cannot be answered away; it still requires its explicit `agent approve` command.

Recovery state includes the current role, exact sanitized error type and cause, failure class, selected action, attempt budget, resume checkpoint, next retry time in UTC, branch, worktree, and worker-service health.

## Inspect and control recovery

```sh
agent failures
agent failures --run-id <run-id> --json
agent dead-letters
agent retry <run-id>
agent resume <run-id>
agent abort <run-id>
agent worker status
agent worker start
agent worker restart
agent worker stop
```

`retry` and `resume` enqueue the existing run and preserve its task worktree. They reject an active lease. An approval-gated run must use `agent approve`, which consumes its exact scoped grant once. `abort` marks a non-active run and queue task `cancelled`; for an active run its durable flag makes the owning worker terminate the complete process group, persist the cancellation checkpoint, release the lease, and retain the worktree. Worker restart uses the bounded graceful shutdown path and expired leases resume the same run.

Role questions and selected recovery states stop the step-level retry loop immediately. Repair loops also stop when the failure and Git diff repeat without progress. This prevents retries that cannot change the outcome.

For the shortest operational path and the meaning of each state, see `docs/operator-runbook.md`.

## Diagnose installation

```sh
agent doctor
agent doctor --full
```

The ordinary check validates project configuration, local trust, Python runtime dependencies, the configured base branch, bundled Harness resources, git state, Codex CLI availability, and worker-service health. It prints concrete `Next:` actions for every actionable problem. `--full` additionally runs the authenticated provider-neutral Runtime preflight and may take longer.
