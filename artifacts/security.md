# Security

## SUMMARY

- Documentation/profile/validator process-only change. No application code, secrets, auth, billing, production settings, or infrastructure changed.

## PROJECT_PROFILE

- Selected profile: `agent_workspace`
- Security commands selected: `make security` via `scripts/security_scan.py`.
- Frontend evidence required: false.

## HIGH

- None.

## MEDIUM

- Hardened autonomy rules affect future commit, push, and PR publication gates. `.agent-policy.yaml` still forbids auto-merge, deploy, force-push, production access, and HIGH-risk publication.
- `scripts/security_scan.py` now checks for obvious private keys, API tokens, credential assignments, private absolute paths, `.env` files, and protected staged files.

## LOW

- None.

## DJANGO_SECURITY_NOTES

- Not applicable.

## SECRETS

- No secrets added.

## DEPENDENCY_RISKS

- No dependency changes.

## RECOMMENDED_ACTION

- Keep control-plane files private by default; keep `make security` clean before autonomous publication.
