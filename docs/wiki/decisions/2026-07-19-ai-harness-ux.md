# AI Harness UX And Local Project Configuration

Date: 2026-07-19

## Decision

The Harness is distributed as the installable Python project `ai-harness` with a user-facing `agent` console command. Daily use is no longer expressed as direct calls to scripts.

The initial CLI surface is:

- `agent init` for idempotent project onboarding;
- `agent task` for normalized, queued task intake;
- `agent status` for read-only project-scoped operational state;
- `agent doctor` for installation and optional authenticated Runtime diagnostics.

## Amendment: explicit lifecycle and executable readiness

Date: 2026-08-05

The CLI now exposes `agent start` and `agent stop` as the ordinary worker-service lifecycle. `agent start` validates local trust, Python runtime imports, and the configured Git base branch before starting workers. `agent doctor` exercises the lazy Python imports required by task intake and prints concrete repair actions, so it cannot report ready immediately before a dependency traceback.

Re-running `agent init` preserves and re-trusts the existing project configuration. `--force` updates explicitly selected configuration while preserving an existing `AGENTS.md` unless `--replace-agents` is also given. Base-branch detection prefers the remote default, then common existing local branches, and task worktrees may use a committed local base when no origin exists.

## Project-local state

`agent init` creates `.agent/project.yaml` and `AGENTS.md` in the target project. The config identifies the project, active profile, base branch, task branch prefix, and Step 2 runtime provider.

Project-local configuration is trusted only for explicit local task execution after the user invokes `agent init`. It is not a publication or capability authority. Push, PR creation, merge, deployment, protected paths, tool credentials, and network side effects remain governed by the central Harness registry and policy.

## 2026-08-06 task-start refinement

`agent task` is now the normal autonomous start path. It starts the persistent worker service when needed and, by default, creates or selects a dedicated task branch in the current clean checkout. Isolated worktrees are available only through the explicit `--worktree` option for parallel execution. An already prepared branch remains available through `--current-branch`.

Project-local generated branch prefixes are validated as Git-safe values instead of being restricted to a small hard-coded list. Central publication policy remains independent: accepting a local task prefix does not grant push, PR, merge, deployment, or protected-path authority.

Explicit consent is recorded outside the repository in the current user's private Harness configuration and binds the resolved repository path to the `.agent/project.yaml` fingerprint. A repository cannot gain execution trust by shipping that file in git; moving or changing it requires another `agent init`.

## Packaging

`pyproject.toml` owns package metadata and the `agent` entry point. A wheel bundles the Harness scripts, schemas, prompts, and policy files in the isolated environment's `share/ai-harness` directory. The CLI resolves a source checkout first during development and the bundled directory under pip/pipx in installed use.

As of 2026-08-06, ordinary users enter this packaging layer through `./install.sh` and refresh it through `agent update`; pipx remains the isolated implementation mechanism rather than a required operating interface. The detailed safety contract is recorded in `2026-08-06-ordinary-user-install-update.md`.

Runtime abstraction is unchanged: `agent task` creates a Task; workers execute model-backed roles through `Runtime.execute(...)`; Codex CLI remains the only Step 2 provider.

## Consequences

- A user installs the Harness once and initializes multiple independent repositories without submodules.
- Explicit task ids preserve queue idempotency.
- Status reads do not create queue state and do not expose transcripts.
- Repositories can be onboarded for local task execution without editing the Harness's central project-profile file.
- Publication still requires central trust and cannot be granted by committing a permissive `.agent/project.yaml`.
