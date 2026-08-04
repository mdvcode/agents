# AI Harness CLI

The Harness is installable as the `ai-harness` Python distribution and exposes one command: `agent`.

## Install

From this repository:

```sh
pipx install .
agent --version
```

For development, use `pip install -e .`. After the distribution is published, the equivalent global install is `pipx install ai-harness`.

The installation bundles the policy, workflow, schemas, prompts, scripts, and sole Step 2 Codex CLI runtime under the isolated pipx environment. `AI_HARNESS_HOME` may point at a source checkout when developing or diagnosing a custom installation.

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

Existing files are preserved. Use `--force` only when replacement is intentional. Profile detection recognizes this agent workspace, Django projects, and Next.js/web projects; it can also be selected explicitly with `--profile`.

`.agent/project.yaml` is local execution identity, not publication authority. It may select the project profile, base branch, task branch prefix, and `codex-cli` runtime. It cannot authorize push, PR publication, merge, deployment, credentials, protected paths, or a different model provider.

`agent init` also records the absolute repository path and configuration fingerprint in the current user's private Harness config. A copied or merely committed `.agent/project.yaml` is not execution authority; after moving or editing the project config, run `agent init` again.

## Create a task

```sh
agent task "Fix login"
agent task --repo . --task-id fix-login "Fix login"
```

The command normalizes the request into the existing Task envelope and idempotently enqueues it in the Harness SQLite queue. Repeating the same explicit `--task-id` returns the existing queue item. Generated branches use the prefix selected during initialization and never use the default branch.

Use `--dry-run --json` to inspect the envelope without changing queue state.

## Inspect status

```sh
agent status
agent status --json
```

Status is read-only, project-scoped, and compact. It shows queue, run, and worker-service states without role transcripts, source contents, credentials, or raw events. If the worker service is not running, tasks remain visibly queued; the CLI does not start a daemon as a hidden side effect.

Recovery state includes the current role, exact sanitized error type and cause, failure class, selected action, attempt budget, resume checkpoint, next retry time in UTC, and worker-service health.

## Inspect and control recovery

```sh
agent failures
agent failures --run-id <run-id> --json
agent dead-letters
agent retry <run-id>
agent resume <run-id>
agent abort <run-id>
```

`retry` and `resume` enqueue the existing run and preserve its task worktree. They reject an active lease. An approval-gated run must use `agent approve`, which consumes its exact scoped grant once. `abort` marks a non-active run and queue task `cancelled`; it does not delete the run evidence or worktree.

## Diagnose installation

```sh
agent doctor
agent doctor --full
```

The ordinary check validates project configuration, local trust, bundled Harness resources, git state, Codex CLI availability, and worker-service health. `--full` additionally runs the authenticated provider-neutral Runtime preflight and may take longer.
