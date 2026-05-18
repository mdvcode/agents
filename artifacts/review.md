# SUMMARY
- Fixed Celery admin status detection for the py311 contactapi2 worker command shape.
- No server-side manual service change was made.
- No migrations, auth/permission/session/CSRF, billing, payment, protected secret, production settings, or production infrastructure files were changed.

# SERVER_FINDINGS
- SSH check on `contactapi.static.fyi` at 2026-05-08 11:21 CEST found the py311 contactapi worker running as user `daryna`.
- The py311 worker command includes `/var/www/hosts/contactapi.static.fyi/contactapi/venv311/bin/python ../venv311/bin/celery -A contactapi worker -P solo -Q py311test`.
- Production `contactapi.celery.service`, `contactapi.celery-high-priority.service`, and `contactapi.celery-low-priority.service` are active.
- No contactapi Celery Beat process was found; the only `beat` process in the checked output belonged to unrelated IAM2.

# CORRECTNESS_FINDINGS
- Existing exact `CELERY_BINARY_PATH` matching remains supported.
- Relative Celery commands such as `../venv311/bin/celery -A contactapi` are now detected.
- Unrelated Celery apps are excluded by requiring the app selector to resolve to `contactapi`.
- Duplicate process entries are avoided by pid when combining exact-path and fallback matches.

# TEST_FINDINGS
- Focused helper tests passed: `4 passed`.
- Repository-level `make check` and `make security` still fail on existing baseline issues recorded in `artifacts/quality.json` and `artifacts/security.md`.

# KNOWN_LESSON_CONFLICTS
- None found. The patch is narrowly scoped, does not alter migrations or production settings, and does not change Celery task execution semantics.
