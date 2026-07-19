# AI Harness UX And Local Project Configuration

Date: 2026-07-19

## Decision

The Harness is distributed as the installable Python project `ai-harness` with a user-facing `agent` console command. Daily use is no longer expressed as direct calls to scripts.

The initial CLI surface is:

- `agent init` for idempotent project onboarding;
- `agent task` for normalized, queued task intake;
- `agent status` for read-only project-scoped operational state;
- `agent doctor` for installation and optional authenticated Runtime diagnostics.

## Project-local state

`agent init` creates `.agent/project.yaml` and `AGENTS.md` in the target project. The config identifies the project, active profile, base branch, task branch prefix, and Step 2 runtime provider.

Project-local configuration is trusted only for explicit local worktree execution after the user invokes `agent init`. It is not a publication or capability authority. Push, PR creation, merge, deployment, protected paths, tool credentials, and network side effects remain governed by the central Harness registry and policy.

Explicit consent is recorded outside the repository in the current user's private Harness configuration and binds the resolved repository path to the `.agent/project.yaml` fingerprint. A repository cannot gain execution trust by shipping that file in git; moving or changing it requires another `agent init`.

## Packaging

`pyproject.toml` owns package metadata and the `agent` entry point. A wheel bundles the Harness scripts, schemas, prompts, and policy files in the isolated environment's `share/ai-harness` directory. The CLI resolves a source checkout first during development and the bundled directory under pip/pipx in installed use.

Runtime abstraction is unchanged: `agent task` creates a Task; workers execute model-backed roles through `Runtime.execute(...)`; Codex CLI remains the only Step 2 provider.

## Consequences

- A user installs the Harness once and initializes multiple independent repositories without submodules.
- Explicit task ids preserve queue idempotency.
- Status reads do not create queue state and do not expose transcripts.
- Repositories can be onboarded for isolated local execution without editing the Harness's central project-profile file.
- Publication still requires central trust and cannot be granted by committing a permissive `.agent/project.yaml`.
