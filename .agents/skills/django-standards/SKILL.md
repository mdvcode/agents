# Django Standards Skill

## Rules
- Do not add new business logic to views or admin unless that pattern already exists nearby.
- Optimize ORM access patterns and avoid queryset inefficiency.
- Respect transaction boundaries.
- Preserve the existing settings module structure and `manage.py` boot behavior.
- Treat migrations as high risk.
- Treat destructive queryset updates or deletes as high risk.
