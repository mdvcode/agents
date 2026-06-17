# Security

## SUMMARY

- Documentation/profile/process-only change. No application code, secrets, auth, billing, production settings, or infrastructure changed.

## PROJECT_PROFILE

- Selected profile: `agent_workspace`
- Security commands selected: none required by `.agent-project-profiles.yaml`; `make security` was used as the repository placeholder.
- Frontend evidence required: false.

## HIGH

- None.

## MEDIUM

- Project profile rules affect future command selection and publication gates. `.agent-project-profiles.yaml` separates agent workspace, Django, and Flowfox checks and keeps HIGH-risk/protected publication gates under `.agent-policy.yaml`.

## LOW

- None.

## DJANGO_SECURITY_NOTES

- Not applicable.

## SECRETS

- No secrets added.

## DEPENDENCY_RISKS

- No dependency changes.

## RECOMMENDED_ACTION

- Keep control-plane files private by default; use the selected project profile before running checks or publishing future work.
