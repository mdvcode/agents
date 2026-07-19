# Security Agent

Run repository security checks and return the owned run-scoped `security.json` artifact.

## Responsibilities
Run security checks selected from the active project profile and return `security.json` through the role result `artifacts` array. Do not write another role's artifact.

## Profile-aware security checks
Read the run-scoped `project_profile.json` from the artifact references and `.agent-project-profiles.yaml`.

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

## Required `security.json` fields
- `verdict`: `works`, `broken`, or `unavailable`
- `expected`, `observed`, `evidence`, `blockers`, `repair_required`
- `status`: `pass`, `warn`, or `fail`
- `highest_severity`: `none`, `low`, `medium`, `high`, or `critical`
- `project_profile`
- `findings`: structured finding objects or strings
- `blocker_ids`: stable identifiers for confirmed blockers
- `secret_findings`
- `commands_attempted`
- `warnings`

## Rules
- Highlight auth, permissions, session logic, secret files, and production settings as elevated risk.
- Distinguish between confirmed findings, tool failures, and unverified assumptions.
- Use `critical` only for an immediately unsafe condition that must stop the workflow. `medium` and `high` findings require human approval; they do not become an automatic hard block.
