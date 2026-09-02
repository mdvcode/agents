# Tweebit AI Harness by Daryna CLI

The Harness is installable as the `ai-harness` Python distribution and exposes one command: `agent`.

## Install

Tweebit v0.3.0 is currently a local, unpublished release candidate. Install it only from the exact
reviewed local checkout:

```sh
cd /absolute/path/to/reviewed/tweebit-checkout
./install.sh
```

The installer checks Python 3.11+, installs pipx when necessary without `sudo`, installs the isolated application, verifies `agent`, and prints the first-use commands. For an existing installation, keep the source explicit while this candidate is unpublished:

```sh
agent update --source /absolute/path/to/reviewed/tweebit-checkout
hash -r
agent doctor --full
```

The remote bootstrap below installs the public `mdvcode/agents` baseline, not this local Tweebit candidate:

```sh
curl -fsSL https://raw.githubusercontent.com/mdvcode/agents/main/install.sh | sh
```

After Tweebit is published from a reviewed source, ordinary updates may again use the product command:

```sh
agent update
agent doctor --full
```

`agent update` uses the installed package source. A clean Git checkout is updated with a fast-forward-only pull; a ZIP/folder installation moves to the official public repository source; a remote package installation is upgraded in place. It then verifies the new `agent` command and restarts the background worker. A dirty source checkout is never overwritten. Consequently, this unpublished candidate must continue to use `agent update --source /absolute/path/to/reviewed/tweebit-checkout` rather than a source-less update.

An installation old enough not to recognize `agent update` must be bootstrapped once. For this
candidate, use `install.sh` from the reviewed local Tweebit checkout. The remote installer can only
bootstrap the public baseline; after that, select Tweebit explicitly with `agent update --source
/absolute/path/to/reviewed/tweebit-checkout`. Refresh the shell command cache with `hash -r`.

Contributors may still use `pip install -e .` or direct pipx commands, but users do not need to manage those environments themselves.

The installation bundles the policy, workflow, schemas, prompts, scripts, official Python Codex SDK, and CLI compatibility adapter under an isolated environment. `AI_HARNESS_HOME` may point at a source checkout when developing or diagnosing a custom installation.

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

`.agent/project.yaml` is local execution identity, not publication authority. It may select the project profile, base branch, task branch prefix, and local-subscription Codex runtime. It cannot authorize push, PR publication, merge, deployment, credentials, protected paths, or a different model provider.

`agent init` also records the absolute repository path and configuration fingerprint in the current user's private Harness config. A copied or merely committed `.agent/project.yaml` is not execution authority; after moving or editing the project config, run `agent init` again.

## Create a task

The ordinary visual entry point is:

```sh
agent dashboard
```

It starts an authenticated loopback control center for the initialized project and opens it in the default browser. The page launches tasks through the same CLI policy boundary and healthy worker as `agent task`, refreshes live task/worker state, displays bounded attention questions, and provides answer, approval, retry, and abort controls. **Очистить историю** hides only completed and cancelled task rows in that browser; active or actionable tasks remain visible, queue/run evidence is retained, and **Вернуть скрытые** restores the rows. The temporary token is passed in the URL fragment, moved to session storage, and removed from the visible URL. Use `Ctrl+C` to stop only the dashboard server.

Validate the installation once after installation or an upgrade:

```sh
agent doctor --full
```

```sh
agent task "Fix login"
agent task --mode adaptive "Fix a small bug with the minimum safe workflow"
agent task --mode fast "Fix a small local issue"
agent task --mode full "Refactor the authentication architecture"
agent task --mode goal "Complete a checkpointed multi-hour objective"
agent task --repo . --task-id fix-login "Fix login"
agent task --current-branch --task-id fix-login "Fix login"
agent task --worktree --task-id parallel-fix "Run this task in parallel"
agent watch --task-id fix-login
```

If an older installed command does not list `adaptive`, install the current checkout with
`agent update --source /path/to/agents`, refresh the shell command cache with `hash -r`, and check
`agent task --help` again.

`agent task` is the ordinary start command: it validates the request, rejects a known stale source/install combination, prepares the task workspace, idempotently enqueues the normalized Task envelope, and verifies that the persistent worker is ready. Repeating the same explicit `--task-id` returns the existing queue item. If worker startup fails after queueing, the run is preserved and the error prints its run id plus the restart/watch commands needed to continue it.

Task mode defaults to `auto`. Auto uses the current guarded fast path unless the goal names a sensitive or broad change such as authentication, migrations, payments, production, dependencies, architecture, or a refactor; it never selects a multi-hour mode or Adaptive before acceptance. `--mode adaptive` explicitly opts into deterministic task analysis and an auditable minimum-safe execution DAG. It may skip optional roles, use deterministic verification instead of optional model calls, run independent read-only checks in parallel, and provide each model-backed role only its scoped context. Low-confidence or sensitive analysis expands to a safer workflow, and hard security, approval, recovery, and publication gates remain mandatory.

Adaptive Acceptance is intentionally separate from task execution. Until representative paired Full/Adaptive evaluation passes, the dashboard reports `NOT ENOUGH DATA` and `auto` keeps the established routing policy; explicit Adaptive tasks remain available. Fast mode invokes only implementation and review models; context, quality, security, and verdict stages are deterministic. It escalates to the full workflow when the resulting patch exceeds five files or 200 changed lines, touches protected areas, or reports increased risk. The complete fast workflow has a 15-minute budget, while `full` runs the complete specialist chain for at most 60 minutes. Use `--mode goal` only for an explicit checkpointed objective that may run for up to 4 hours. The separate 30-minute role timeout bounds one model executor and does not define total task duration.

By default, the command creates a fresh dedicated task branch in the current checkout from the configured base branch. It never silently reuses an existing branch and does not create a worktree. Repeating the same queued task id is idempotent; intentional work on an existing branch requires `--current-branch`. If queueing fails before ownership is recorded, a newly created clean branch is rolled back. The checkout must be clean. Setup files intentionally ignored by Git may stay local and must not be force-added; otherwise commit or intentionally ignore new setup files and commit or stash other intended changes before starting the first task. Only one unfinished current-checkout task may own a repository at a time.

Generated branches use the prefix selected during initialization and never use the default branch. The prefix is not restricted to a fixed list: any safe Git prefix is accepted, including `feat/`, `fix/`, `chore/`, `release/2026/`, or `team/mobile/`:

```sh
agent init --force --branch-prefix chore/
```

Prompt length and punctuation never become a branch-name failure: generated names are normalized, bounded, and receive a deterministic fallback automatically. Existing branches are checked against Git's own ref rules rather than a narrower ASCII-only list, so valid names containing Unicode or punctuation such as `+`, `=`, `&`, and `,` are accepted. Ambiguous or unsafe ref forms such as `../`, `@{`, repeated `/`, control characters, and `.lock` remain blocked.

`--current-branch` uses an already checked-out clean non-default branch without creating or renaming it. `--worktree` is the explicit opt-in for isolated parallel task execution. The worker revalidates current-checkout branches immediately before execution. Status, retry, resume, and abort preserve the same authoritative run and workspace.

## Submit a task batch

Use one manifest when several repositories or independent branches should be scheduled together:

```yaml
version: 1
repositories:
  backend:
    path: /projects/backend
    max_parallel_tasks: 3
  frontend:
    path: /projects/frontend
    max_parallel_tasks: 2
tasks:
  - repo: backend
    goal: Fix report export
  - repo: backend
    goal: Add report filters
    parallel: true
  - repo: frontend
    goal: Fix the navigation menu
```

```sh
agent batch --file tasks.yaml
agent batch --file tasks.yaml --dry-run --json
cat tasks.yaml | agent batch --file -
```

The dashboard accepts the same YAML, and the loopback API accepts either a `manifest` string or a `repositories` plus `tasks` object at `POST /tasks/batch`. In visual intake, `parallel: true` automatically selects an isolated worktree. The ordinary CLI keeps `--worktree` explicit. A repository limit is enforced atomically when workers claim tasks, so several workers cannot oversubscribe one project or its shared test resources.

Worktrees reuse private shared pip, uv, npm, and Bun download caches. Project-specific Turbo, build, virtualenv, and container-layer cache roots are also stable across task worktrees; a task still owns a separate checkout and branch.

Single-checkout mode treats submission of a new task as replacement of an older human-paused, blocked, dead-lettered, or failed task. The old queue item is cancelled, but its Git branch and run files are preserved; the new task is then prepared and queued normally. Active or merely queued work is never replaced automatically. Advanced users may pass `--keep-paused` to retain the previous pause and refuse the new task instead.

Use `--dry-run --json` to inspect the envelope without switching branches, starting workers, or changing queue state. `agent start` remains available for proactively starting the service, but it is no longer required before `agent task`.

`agent watch` follows the queue and current role until the task completes or needs attention. It prints only state transitions, so a long-running role remains identifiable without dumping transcripts. It returns control after 30 minutes by default while leaving the task running. Set `--timeout <seconds>` to choose another bound; zero is an explicit indefinite wait. If the worker service is not running, `watch` returns immediately with the recovery command instead of waiting silently.

## Inspect status

```sh
agent status
agent status --json
```

Status is read-only, project-scoped, and compact. It shows queue, run, and worker-service states without role transcripts, source contents, credentials, or raw events. Worker startup is an intentional side effect of `agent task`; read-only commands never start it.

The dashboard provides one cross-repository queue with `Queued`, `Running`, `Testing`, `Needs input`, `PR ready`, and `Failed` lifecycle views. Repository, task branch, and worker filters can be combined. When active branches in one repository currently touch the same paths, both tasks receive a probable-conflict marker and a deterministic publish-first/rebase-second recommendation.

During implementation, a role may propose a bounded independent child task. The deterministic router—not the model—decides whether it is allowed. A writing child receives its own worktree, branch, SDK thread, token/time budget, and `allowed_paths`; the parent records `root_run_id` and `parent_run_id`. Blocking failures stay in the normal sequential repair loop. Independent children may run in the background, after which the parent alone joins their patch, resumes its original SDK thread, runs the combined verification, and owns publication. Fan-out is limited to three children and graph depth to two levels.

When execution cannot continue autonomously, status and watch print an `ATTENTION REQUIRED` block with the exact question or cause, current role, and next command. If information is missing, answer it without creating a new run:

```sh
agent answer <run-id> "Use the staging API and JSON output"
```

When the role provides a small set of choices, status/watch list the recommended option first and the dashboard shows a dropdown with descriptions and a `Другой ответ` choice. After an answer, the same question cannot open another answer gate: a repeated question stops with an explicit diagnostic instead of looping silently.

The answer is sanitized, stored only in the private run directory, made available to the resumed role, and resumes the same checkpoint. Do not include passwords, tokens, or customer secrets. A risk, security, protected-path, or publication decision cannot be answered away; it still requires its explicit `agent approve` command.

Recovery state includes the current role, exact sanitized error type and cause, failure class, selected action, attempt budget, resume checkpoint, next retry time in UTC, canonical checkout/branch identity, and worker-service health. Active status and watch output additionally show the current phase, latest SDK event, active tool, seconds since progress, used/maximum token budget, and stop reason.

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

`retry` and `resume` enqueue the existing run and preserve its `checkout_path`, `task_branch`, and run-bound SDK thread. They reject an active lease. An approval-gated run must use `agent approve`, which consumes its exact scoped grant once. `abort` marks a non-active run and queue task `cancelled`; for an active run its durable flag makes the owning worker terminate the complete process group, persist the cancellation checkpoint, release the lease, and retain the checkout. Worker restart uses the bounded graceful shutdown path and expired leases resume the same run.

Role questions and selected recovery states stop the step-level retry loop immediately. Repair loops also stop when the failure and Git diff repeat without progress. This prevents retries that cannot change the outcome.

For the shortest operational path and the meaning of each state, see `docs/operator-runbook.md`.

## Diagnose installation

```sh
agent doctor
agent doctor --full
```

The ordinary check validates project configuration, local trust, Python runtime dependencies, the configured base branch, bundled Harness resources, git state, Codex CLI availability, and worker-service health. It prints concrete `Next:` actions for every actionable problem. `--full` additionally runs the authenticated provider-neutral Runtime preflight and may take longer.
