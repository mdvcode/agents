# Summary
- Investigated the contactapi2 admin Celery Scheduler status against the real server state.
- Found a code-side detection bug: py311 Celery is running, but the admin helper only matched an absolute `settings.VIRTUALENV_DIR/bin/celery` cmdline entry.
- Patched `celery_task_get_processes()` to keep the exact-path match and add a contactapi-specific fallback for relative Celery commands.
- Added focused tests for absolute path matching, py311 relative command matching, unrelated Celery exclusion, and duplicate avoidance.

# Root Cause
- On contactapi2, the py311 worker process is launched with `../venv311/bin/celery`.
- `settings_py311.VIRTUALENV_DIR` resolves to the absolute `.../venv311`, so the old helper looked for `.../venv311/bin/celery` and missed the live process.
- Because `celery_task_get_admin_context()` uses that helper directly, the admin UI could show `Inaktiv` and `Starten` even while the py311 worker was running.

# File Changes
## Modified
- `contactapi/apps/core/utils/celery_process_helper.py`: added robust contactapi Celery cmdline matching.
- `artifacts/plan.md`, `artifacts/risk.json`, `artifacts/review.md`, `artifacts/quality.json`, `artifacts/security.md`, `artifacts/verdict.json`, `artifacts/report.md`: updated for this task.
- `artifacts/audit_log.jsonl`: appended this autonomous action.

## Added
- `contactapi/apps/core/tests/test_celery_process_helper.py`: focused regression tests.

# Verification
- Server check: py311 contactapi worker is running; no contactapi beat process was found.
- Passed: `python3 -m py_compile contactapi/apps/core/utils/celery_process_helper.py contactapi/apps/core/tests/test_celery_process_helper.py`.
- Passed: `./.venv38/bin/python -m pytest -p no:django contactapi/apps/core/tests/test_celery_process_helper.py --tb=short --maxfail=1 -o addopts=` with `4 passed`.
- Passed: `./.venv38/bin/python -m ruff check contactapi/apps/core/tests/test_celery_process_helper.py`.
- Failed baseline: `make check` fails on existing broad ruff/format findings, `contactapi/apps/campaigndata/models.py:42` syntax error, and pytest startup failure due missing `pyairtable`.
- Failed baseline: `make security` fails on existing Bandit and pip-audit findings.

# Next Action
- Deploy the code patch to the py311 test stand and refresh `/admin/`; the status should detect the existing `py311test` worker as active.
- Separately decide whether the UI label should be changed from "Scheduler" if the desired status is specifically Celery Beat, because no contactapi beat process was present in the server check.
