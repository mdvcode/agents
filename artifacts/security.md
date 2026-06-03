# Security

## Checks

- PASS: `detect-secrets scan <changed safe files>` produced no findings.
- BLOCKED BASELINE: `bun audit` reports 105 existing vulnerabilities, including 2 critical. No dependency files were changed.

## Protected Paths

- No protected paths touched.
- No migrations, auth/session rules, billing, payments, secrets, dependency manifests, webhooks, or production infrastructure changed.

## Notes

- Studio view URLs are built from existing Sanity slug values and encoded public paths.
- Missing slugs disable the action instead of opening a malformed URL.
