# Ordinary-User Installation And Updates

Date: 2026-08-06

## Decision

Installation and updates are product operations, not Python-environment instructions. The supported first-install entry point is `./install.sh` from a downloaded folder or the same script fetched from the official repository. The supported update entry point is `agent update`.

The installer requires Python 3.11 or newer, installs pipx without `sudo` when it is absent, installs the application into an isolated environment, verifies the resulting `agent` executable, and prints the first-use commands. pipx remains an internal packaging mechanism rather than required user knowledge.

`agent update` reads the source recorded by pipx. A local Git checkout must be clean and is updated only with `git pull --ff-only`; a downloaded non-Git folder moves to the official repository source; a remote installation is upgraded in place. An explicit `--source` may select a separately downloaded Harness folder or Git source. After the package refresh, the new command is verified and the worker service is restarted.

## Safety boundaries

- No `sudo`, global site-package mutation, merge, reset, or force-pull is used.
- Setup files intentionally ignored by the target repository stay local; installation and initialization never recommend force-adding them.
- A dirty source checkout stops with exact remediation instead of losing changes.
- The worker restarts only after package installation succeeds.
- Package success with worker restart failure is reported as `updated_with_warning`, with a concrete recovery command.
- Project execution and publication authority are unchanged; installation does not grant repository trust or side-effect permissions.

## Consequences

- The ordinary lifecycle is download, `./install.sh`, `agent init`, `agent task`, `agent watch`, and later `agent update`.
- Installations that predate `agent update` use the current installer once as the compatibility bootstrap.
- Documentation no longer asks ordinary users to diagnose or operate pipx directly.
- Contributors may still use editable Python installations without changing the supported user path.
