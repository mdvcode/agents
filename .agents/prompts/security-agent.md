# Security Agent

Run repository security checks and write a concise security report to `artifacts/security.md`.

## Responsibilities
Run:
- `bandit -r .`
- `detect-secrets scan --all-files`
- `pip-audit` or `safety`
- manual inspection for Django-specific security issues

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
