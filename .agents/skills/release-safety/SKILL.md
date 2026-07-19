---
name: release-safety
description: "Apply release and deployment safety gates for agent-managed changes."
---
# Release Safety Skill

## Rules
- Never auto-deploy medium-risk or high-risk changes.
- Protected paths disable autonomous deploy.
- Require smoke tests after staging.
- Require a rollback artifact on deploy failure.
- Do not declare deployment success if the deploy command was skipped or not configured.
