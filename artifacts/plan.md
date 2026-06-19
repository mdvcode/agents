# Goal

## GOAL

- Implement the remaining P2.4 foundation items and a minimal executable P3 agent workflow skeleton:
  - trusted repository registry;
  - change-set completeness checks;
  - CI changed-files security scanning;
  - run-scoped artifact support;
  - task worktree bootstrap;
  - executable role chain with checkpoints;
  - specialist role prompts for frontend QA, architecture consistency, semantic conflict, CI repair, context compiling, and evals.

## PROJECT_PROFILE

- Selected profile: `agent_workspace`
- Reason: changes private agent harness scripts, docs, prompts, workflows, policies, and artifacts.
- Quality commands: `make check`
- Security commands: `make security`

## RISK

- MEDIUM: this extends deterministic harness automation and publication guardrails, but does not touch production infrastructure, credentials, auth, billing, migrations, auto-merge, or deployment.

## PLAN

1. Add `.agent-repositories.yaml` as trusted repository registry.
2. Validate registry structure in `scripts/validate_artifacts.py`.
3. Make `publish_pr.py` consult the registry for trusted remotes, profile, base branch, and allowed branch prefixes.
4. Add `--artifacts-dir` and `--run-id` to `publish_pr.py`.
5. Add change-set completeness checks against real changed files and `risk.changed_areas`.
6. Add `security_scan.py --base-ref --head-ref` for CI changed-file scanning.
7. Update GitHub Actions to use changed-file scanning.
8. Add `scripts/worktree_manager.py` for task-start worktree creation.
9. Add `scripts/agent_role_runner.py` with deterministic role checkpoints and optional command adapter.
10. Add missing specialist role prompts.
11. Extend `.agent-workflows.yaml` with `full_agent_workflow`.
12. Add tests for registry validation, changed-file security scanning, run-scoped role artifacts, branch policy, and publication guardrails.

## VERIFY

- `python3 -m py_compile scripts/*.py`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests`
- `make validate-artifacts`
- `make security`
- `make check`
- `git diff HEAD --check`
