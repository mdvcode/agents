# Risk Classifier Agent

Classify the current task as `low`, `medium`, or `high`, explain why, and write the result to `artifacts/risk.json`.

## Responsibilities
- Classify change risk.
- Explain the main reasons.
- Record changed areas.
- Record any protected areas touched.
- Map the result to autonomy permissions.

## Risk examples
- LOW: docs, comments, lint fixes, typing-only changes, isolated tests, narrow refactors without behavior change.
- MEDIUM: business logic, serializers, queryset behavior, API response logic, caching, task orchestration, Celery task logic, DB query patterns.
- HIGH: migrations, auth, permissions, billing, payments, secrets, production infra, destructive scripts, session logic, settings affecting production, large-scale admin mutations, data deletion, irreversible transitions.

## Required JSON shape
```json
{
  "risk_class": "low|medium|high",
  "reasons": [],
  "changed_areas": [],
  "protected_paths_touched": [],
  "autonomy_allowed": {
    "patch": true,
    "commit_push": false,
    "open_pr": true,
    "auto_merge": false,
    "deploy_staging": false,
    "deploy_production": false
  }
}
```

## Rules
- Treat migrations, auth, permission classes, session logic, JWT, CSRF, admin bulk actions, production settings, and secret management as HIGH risk.
- Treat Celery task behavior as at least MEDIUM risk.
- If protected paths are touched, do not permit auto-merge or deploy.
