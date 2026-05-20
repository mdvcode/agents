# Risk Graph

## High Risk
- Migrations.
- Auth, permissions, sessions, JWT, CSRF.
- Billing, payments, secrets, credentials.
- Production settings and production infrastructure.
- Destructive scripts or queryset mutations.

## Medium Risk
- Business logic.
- Serializer behavior.
- Queryset behavior.
- API response changes.
- Caching.
- Celery task behavior.

## Low Risk
- Documentation.
- Local agent process.
- Artifact schemas.
- Non-destructive validation tooling.
- Isolated tests.
