TASK
- P3.2: deterministic dynamic routing and bounded repair loops.

PROJECT_PROFILE
- Selected profile: agent_workspace.
- Reason: task changes the local agent harness routing policy, workflow state, scripts, schemas, tests, docs, and run artifacts.
- Quality commands: focused router and runner pytest; full pytest; `make validate-artifacts`; `make check`.
- Security commands: `make security`.
- Frontend evidence required: no.

CONTEXT
- The prior cleanup removed obsolete project coupling and left a generic agent control plane.
- Role results still expose `next_action`, and `scripts/agent_role_runner.py` currently uses it as the routing source.
- P3.2 must make routing authoritative in code and policy while preserving the existing role-result contract as an advisory output.
- The router must enforce required gates, risk/security stops, relevant optional gates, bounded repair loops, repeated-failure stops, budgets, and trace events.

FILES_TO_INSPECT
- `.agent-policy.yaml`
- `.agent-project-profiles.yaml`
- `.agent-role-contracts.yaml`
- `.agent-workflows.yaml`
- `scripts/agent_role_runner.py`
- `scripts/run_workflow.py`
- `scripts/validate_artifacts.py`
- `schemas/*.json`
- `schemas/roles/*.json`
- `tests/test_agent_role_runner.py`
- `tests/test_run_workflow.py`
- `docs/agent-system.md`
- `docs/kanban/tasks.md`

FILES_TO_CHANGE
- `.agent-routing.yaml`
- `.agent-workflows.yaml`
- `scripts/workflow_router.py`
- `scripts/agent_role_runner.py`
- `scripts/run_workflow.py`
- `schemas/agent_workflow.schema.json`
- `schemas/workflow_route.schema.json`
- `scripts/validate_artifacts.py`
- `tests/test_workflow_router.py`
- `tests/test_bounded_repair_loops.py`
- `tests/test_agent_role_runner.py`
- `docs/agent-system.md`
- `docs/kanban/tasks.md`
- current P3.2 artifacts and audit log

DO_NOT_TOUCH
- Do not change publication, merge, deployment, auth, billing, migration, or secret-management behavior outside the routing scope.
- Do not let the router publish or auto-merge; it may only select `publication-prepare` after gates pass.
- Do not treat the LLM advisory `next_action` as an authority.

ASSUMPTIONS
- Existing role names and role-result schemas remain stable; the router is additive and authoritative.
- Optional frontend, architecture, and semantic gates are skipped only when their deterministic conditions are false.
- A failed quality/review/CI result may return to implementation or quality only within the configured loop budget.

CHECKS_TO_RUN
- `make validate-artifacts`
- `make security`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_workflow_router.py tests/test_bounded_repair_loops.py tests/test_agent_role_runner.py tests/test_run_workflow.py -q`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests`
- `make check`

INITIAL_RISK_CLASS
- Medium.
- Rationale: this changes the workflow state machine, bounded retries, and publication reachability in the private control plane. It does not touch auth, billing, migrations, production settings, deployment infrastructure, or secrets.

DONE_CRITERIA
- `.agent-routing.yaml`, router, route schema, and workflow state schema exist and validate.
- LLM `next_action` is advisory only and cannot skip required gates.
- HIGH risk/security blockers stop before publication; LOW/MEDIUM reach publication preparation only after required gates.
- Relevant optional gates run deterministically; irrelevant gates are skipped.
- Quality/review/CI repair loops are bounded and repeated failures without progress stop at approval.
- Role/time/token/repair budgets are enforced and routing decisions are traced.
- Router tests, artifact validation, security checks, full tests, and `make check` pass.
