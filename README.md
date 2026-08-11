# AI Harness

AI Harness is a policy-governed system for running software tasks in local Git repositories through one command-line interface: `agent`.

It prepares task branches or worktrees, runs bounded implementation and verification workflows, preserves recovery checkpoints, and stops for human input when a decision cannot be made safely. Merge and deployment always remain human actions.

## Requirements

- macOS or Linux
- Git
- Python 3.11 or newer
- Codex CLI with an authenticated local subscription

The installer creates an isolated application environment and does not require `sudo`.

## Install

Download and unpack the repository, open a terminal in that directory, and run:

```sh
cd ~/Downloads/agents-main
./install.sh
```

You can also install directly from the official repository:

```sh
curl -fsSL https://raw.githubusercontent.com/mdvcode/agents/main/install.sh | sh
```

If the shell does not immediately find `agent`, open a new terminal or refresh its command cache:

```sh
hash -r
agent --version
```

## Quick start

Initialize a target project and verify the complete runtime:

```sh
cd /path/to/project
agent init
agent doctor --full
```

Start a task and follow its progress:

```sh
agent task "Fix startup and add a regression test"
agent watch
```

Or use the local browser dashboard:

```sh
agent dashboard
```

`agent task` starts the background worker automatically when needed. The project checkout must be clean before a task can create or switch branches.

`agent init` creates `.agent/project.yaml` and, when absent, `AGENTS.md`. If Git already ignores either file, keep it local and do not force-add it. Otherwise, commit the new file or add a repository-approved ignore rule before starting work.

## Task execution modes

The default mode is `auto`:

```sh
agent task "Fix a typo in the settings page"
agent task --mode fast "Apply a small local styling change"
agent task --mode full "Refactor the authentication architecture"
```

| Mode | Behavior |
| --- | --- |
| `auto` | Uses the guarded fast workflow for ordinary work and selects the full workflow when the goal names sensitive or broad changes. |
| `fast` | Requests the short workflow with implementation and review as the only model-backed roles. Context, quality, security, and verdict stages are deterministic. |
| `full` | Runs the complete specialist workflow intentionally. |

Fast mode has a 15-minute workflow budget. It automatically escalates to the full workflow before publication if the patch touches protected areas, changes more than five files, exceeds 200 changed lines, or reports increased risk. Required checks and approval gates are never bypassed.

## Branch and workspace modes

By default, a task creates or selects a dedicated task branch in the current checkout:

```sh
agent task --task-id fix-startup "Fix startup"
```

Use the clean, already checked-out non-default branch:

```sh
agent task --current-branch "Continue work on this branch"
```

Use an isolated Git worktree for intentional parallel work:

```sh
agent task --worktree --task-id parallel-fix "Run this task in parallel"
```

Configure generated branch names or a different base branch during initialization:

```sh
agent init --force --base-branch develop
agent init --force --branch-prefix team/backend/
```

## Human attention and recovery

When execution needs information, `agent status` or `agent watch` prints an `ATTENTION REQUIRED` block and the exact next command. Answer the same run without starting a replacement task:

```sh
agent answer <run-id> "Use the existing API and preserve backward compatibility"
agent watch --run-id <run-id>
```

Do not include passwords, tokens, private customer data, or other secrets in an answer.

Risk, security, protected-path, and publication decisions require an explicit scoped approval:

```sh
agent approve --run-id <run-id> --reason "Reviewed the reported scope and risk"
```

Use `answer` for missing information and `approve` only for the exact authority request shown by the system.

## Command reference

Run `agent <command> --help` for the complete generated option list.

### Version and updates

```sh
agent --version
agent update
agent update --source /path/to/new/ai-harness
agent update --json
```

- `agent --version` prints the installed version.
- `agent update` installs the latest version and restarts the worker service.
- `--source` installs an explicitly selected local folder, `git+https`, or `git+ssh` source.

### Project initialization

```sh
agent init [--repo PATH] [--project-id ID]
           [--profile auto|agent_workspace|django|nextjs_web]
           [--base-branch BRANCH] [--branch-prefix PREFIX]
           [--force] [--replace-agents] [--json]
```

- `--profile` selects or auto-detects the project validation profile.
- `--force` replaces `.agent/project.yaml` while preserving an existing `AGENTS.md`.
- `--replace-agents` allows `--force` to replace `AGENTS.md` as well.

### Create a task

```sh
agent task [--repo PATH] [--task-id ID] [--branch BRANCH]
           [--current-branch | --worktree] [--keep-paused]
           [--mode auto|fast|full] [--priority -100..100]
           [--max-retries 0..10] [--dry-run] [--json]
           "TASK DESCRIPTION"
```

- `--dry-run --json` validates and displays the task envelope without switching branches, starting workers, or changing queue state.
- `--keep-paused` prevents a new task from replacing an older paused task that owns the same checkout.
- Reusing the same explicit `--task-id` returns the existing queue item instead of creating a duplicate.

### Worker service

```sh
agent start [--repo PATH] [--workers 1..32] [--json]
agent stop [--json]

agent worker status [--json]
agent worker start [--workers 1..32] [--json]
agent worker restart [--workers 1..32] [--json]
agent worker stop [--json]
```

- `agent start` validates the current project and proactively starts workers. It is optional because `agent task` starts them when necessary.
- `agent stop` and `agent worker stop` gracefully stop the persistent service.
- `agent worker ...` controls the service directly without performing project startup validation.

### Dashboard

```sh
agent dashboard [--repo PATH] [--port PORT] [--no-open]
```

The dashboard binds to loopback, opens in the default browser, and provides task launch, execution-mode (`auto`, `fast`, or `full`) and Git-workspace selection, status, structured answer choices with a custom-answer fallback, approval, retry, and abort controls. Answered questions are fingerprinted so the same question cannot silently reopen in a loop. `--no-open` starts the server without opening a browser. `Ctrl+C` stops the dashboard server but does not stop the worker service.

### Status and monitoring

```sh
agent status [--repo PATH] [--limit 1..100] [--json]

agent watch [--repo PATH] [--task-id ID] [--run-id ID]
            [--interval SECONDS] [--timeout SECONDS] [--json]
```

- `status` shows compact project, queue, run, and worker state without raw model transcripts.
- `watch` follows state transitions until completion or human attention. A timeout of `0` waits indefinitely.

### Failure inspection

```sh
agent failures [--repo PATH] [--run-id ID] [--limit 1..500] [--json]
agent dead-letters [--repo PATH] [--limit 1..500] [--json]
```

- `failures` lists structured failures and their recovery decisions.
- `dead-letters` lists tasks whose bounded automatic recovery budget is exhausted.

### Run recovery and cancellation

```sh
agent retry <run-id> [--repo PATH] [--json]
agent resume <run-id> [--repo PATH] [--json]
agent abort <run-id> [--repo PATH] [--json]
```

- `retry` retries an existing failed or paused run after its underlying problem has been corrected.
- `resume` continues an existing recorded checkpoint.
- `abort` cancels the run, terminates its active process group when necessary, and preserves its branch, worktree, and run records.

### Human response and approval

```sh
agent answer <run-id> "RESPONSE" [--repo PATH] [--actor NAME] [--json]

agent approve [--repo PATH] [--run-id ID]
              [--actor NAME] [--reason TEXT] [--json]
```

- `answer` supplies missing information and resumes the same checkpoint.
- `approve` consumes one pending scoped approval. When only one eligible run exists, `--run-id` may be omitted.

### Diagnostics

```sh
agent doctor [--repo PATH] [--json]
agent doctor --full [--repo PATH] [--json]
```

The ordinary diagnostic checks installation resources, recovery policy, project configuration, local trust, Git state, Python dependencies, Codex CLI availability, and worker health. `--full` additionally performs the authenticated runtime preflight and may take longer.

## Common operating sequence

```sh
agent doctor --full
agent status
agent failures
agent dead-letters
agent worker status
```

If the worker is unhealthy after an update:

```sh
agent worker restart
agent doctor --full
```

Do not edit queue database rows or workflow state files manually. The recovery commands preserve authoritative run identity, checkpoints, leases, and task workspaces.

## Safety model

- The checkout must be clean before branch switching.
- Protected paths, secrets, authentication, billing, payments, migrations, and production infrastructure receive elevated handling.
- Failed or unavailable required checks cannot be published as successful.
- Low- and medium-risk work may be prepared for review only when repository policy allows it.
- The system never auto-merges or deploys.
- Private run state, raw events, and local memory remain in the Harness control plane and must not be copied into public project output.

## Documentation

- [CLI guide](docs/cli.md)
- [Operator runbook](docs/operator-runbook.md)
- [Onboarding](docs/onboarding.md)
- [System architecture](docs/agent-system.md)
- [Policy](.agent-policy.yaml)
- [Project profiles](.agent-project-profiles.yaml)

## Development checks

From this repository:

```sh
make validate-artifacts
make security
make check
```

`make check` validates contracts, runs the security scan and complete test suite, and checks the Git diff for whitespace errors.
