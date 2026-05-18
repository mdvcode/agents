TASK
- Fix the contactapi2 admin Celery Scheduler status so it reflects the real Celery process state on the py311 test stand.

CONTEXT
- Admin index calls `ContactAPIAdminSite.index()`.
- The Celery status block uses `celery_task_get_admin_context()`.
- `celery_task_get_processes()` currently matches only an exact absolute `settings.VIRTUALENV_DIR/bin/celery` cmdline element.
- On `contactapi2`/py311, the live worker process is started with a relative `../venv311/bin/celery` cmdline element, so the exact-path match misses it.
- Server check on 2026-05-08 found:
  - py311 contactapi worker running as `daryna`, queue `py311test`, command includes `-A contactapi`.
  - production contactapi venv38 worker systemd units are active.
  - no contactapi beat process was found; only an unrelated IAM2 beat process was present.

RISK
- MEDIUM: the change affects Celery admin status and start/stop/restart button gating.
- No migrations, auth/session/JWT/CSRF, billing/payments, secrets, production settings, or destructive commands are in scope.

FILES_TO_INSPECT
- `AGENTS.md`
- `artifacts/lessons_learned.md`
- `contactapi/contactapi/admin.py`
- `contactapi/apps/core/utils/celery_process_helper.py`
- `contactapi/apps/core/utils/processes.py`
- `contactapi/templates/admin/contactapi_admin_index.html`
- `contactapi/contactapi/settings_py311.py`
- `contactapi/contactapi/settings_live.py`

IMPLEMENTATION_PLAN
- Keep the existing exact `CELERY_BINARY_PATH` match for compatibility.
- Add a fallback matcher for Celery processes whose cmdline contains a `celery` executable and app selector `-A contactapi` or `--app contactapi`.
- Exclude unrelated Celery apps such as IAM2 by requiring the contactapi app name.
- Add focused tests for exact-path matching, relative py311 command matching, and unrelated app exclusion.
- Leave server processes/systemd untouched.

CHECKS_TO_RUN
- `python3 -m py_compile contactapi/apps/core/utils/celery_process_helper.py contactapi/apps/core/tests/test_celery_process_helper.py`
- `./.venv38/bin/python -m pytest contactapi/apps/core/tests/test_celery_process_helper.py --tb=short --maxfail=1 -o addopts=`
- `make check`
- `make security`

DONE_CRITERIA
- The helper detects the py311 contactapi worker command shape observed on `contactapi2`.
- The helper still detects the existing absolute-path production command shape.
- Unrelated Celery processes are not counted as contactapi Scheduler status.
- Required artifacts and audit log are updated.
