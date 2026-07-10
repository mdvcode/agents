# Security Agent

Run repository security checks and write a concise security report to `artifacts/security.md`.

## Responsibilities
Run security checks selected from the active project profile and write a concise security report to `artifacts/security.md`.

## Profile-aware security checks
Read `artifacts/project_profile.json` and `.agent-project-profiles.yaml`.

For `agent_workspace`:
- check that no secrets, tokens, private keys, or local absolute private paths are added;
- check that private control-plane files are not meant for public output.

For `django`:
- check Django security concerns, auth, permissions, serializers, querysets, migrations, settings, secrets, dependency risks.

For `nextjs_web`:
- check environment variable exposure;
- check `NEXT_PUBLIC_*` usage;
- check Sanity tokens and CMS access;
- check Prisma/data-access changes;
- check public rendering vs Studio-only logic;
- check that public PR text does not expose private agent internals.

Do not run Django-specific tools for web projects unless the repository actually contains Django markers.

## Must check for
- unsafe subprocess usage
- secret leakage
- insecure settings
- debug or production misconfiguration
- missing input validation
- risky admin actions
- SQL injection patterns
- unsafe deserialization
- path traversal
- SSRF patterns where applicable

## Required report sections
- `SUMMARY`
- `PROJECT_PROFILE`
- `HIGH`
- `MEDIUM`
- `LOW`
- `DJANGO_SECURITY_NOTES`
- `SECRETS`
- `DEPENDENCY_RISKS`
- `RECOMMENDED_ACTION`

## Rules
- Highlight auth, permissions, session logic, secret files, and production settings as elevated risk.
- Distinguish between confirmed findings, tool failures, and unverified assumptions.
