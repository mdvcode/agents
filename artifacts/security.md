# SUMMARY
- Security review for the multi-project private memory correction.
- The task changes documentation, local skills, and private project memory templates.
- No secrets, shell execution, eval/exec, database writes, migrations, production settings, or infrastructure files are introduced.

# CHECKS
- `make security`: passed.
- `make check`: passed.
- `python3 scripts/validate_artifacts.py`: passed.
- Manual task-specific security review: passed.

# TASK_SPECIFIC_FINDINGS
- No task-specific security findings.
- Existing `.idea/*` staged files were not modified.
- The change reduces confidentiality risk by making private project memory local-only by default.
- `docs/projects/_template/privacy.md` defines what may and may not be published to target repositories.

# BASELINE_FINDINGS
- No baseline security blocker found for this documentation/tooling-only agent workspace change.

# RECOMMENDED_ACTION
- No task-specific security blocker was found.
