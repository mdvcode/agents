# Security

## Checks

- BLOCKED TOOL MISSING: `detect-secrets scan <changed safe files>` because `detect-secrets` is not installed.
- PASS FALLBACK: marker scan over changed Flowfox and agent files found no secret-like strings.
- BLOCKED BASELINE: `bun audit` reports 105 existing vulnerabilities, including 2 critical. No dependency files were changed.

## Protected Paths

- No protected paths touched.
- No migrations, auth/session rules, billing, payments, secrets, dependency manifests, webhooks, or production infrastructure changed.

## Notes

- The Flowfox code patch removes static JSX only and does not add new input, output, network, storage, or permission behavior.
- Authenticated local `/tools` visual verification was completed using the user-provided test access. No credentials are stored in artifacts or report text.
