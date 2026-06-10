# Security

## SUMMARY

- Documentation/process-only change. No application code, secrets, auth, billing, production settings, or infrastructure changed.

## HIGH

- None.

## MEDIUM

- Flowfox publication rules now allow automated commit, push, and PR creation for completed LOW/MEDIUM issue work without a separate approval step. The policy still blocks HIGH-risk work, protected paths, secrets, private control-plane files, auto-merge, deploy, and public wording that exposes agent/Codex/AI/automation involvement.

## LOW

- None.

## DJANGO_SECURITY_NOTES

- Not applicable.

## SECRETS

- No secrets added.

## DEPENDENCY_RISKS

- No dependency changes.

## RECOMMENDED_ACTION

- Keep control-plane files private by default; commit/push only task-scoped public Flowfox project files after required verification and use normal product/engineering wording.
