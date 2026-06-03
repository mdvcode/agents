# Security

## SUMMARY

- Documentation/prompt-only change. No application code, secrets, auth, billing, production settings, or infrastructure changed.

## HIGH

- None.

## MEDIUM

- None.

## LOW

- Flowfox publication rules now explicitly forbid staging private control-plane files and forbid public commit/PR wording that exposes agent/Codex/AI/automation involvement.

## DJANGO_SECURITY_NOTES

- Not applicable.

## SECRETS

- No secrets added.

## DEPENDENCY_RISKS

- No dependency changes.

## RECOMMENDED_ACTION

- Keep control-plane files private by default; commit/push only approved public Flowfox project files and use normal product/engineering wording.
