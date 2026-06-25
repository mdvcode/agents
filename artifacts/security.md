# Security Review

Risk class: medium.

Review notes:
- `codex_cli_executor.py` now calls commands with argv lists and no `shell=True`.
- Sandbox is mapped from role filesystem access to `read-only` or `workspace-write`.
- Read-only roles are blocked when the git snapshot changes before harness artifact writes.
- Artifact paths are rejected when absolute or containing `..`.
- Non-code artifacts are written only under the run-scoped `artifacts_dir`.
- No secrets, credentials, `.env*`, private keys, auth, billing, payments, migrations, production settings, deployment scripts, or infrastructure files were changed.

Security command:
- `make security` passed: no obvious secrets, private keys, private paths, or protected staged files found.
