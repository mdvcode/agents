---
name: security-checklist
description: "Review common security risks, secrets, auth, settings, and unsafe operations."
---
# Security Checklist Skill

## Rules
- Check patterns static tools may miss.
- Review Django settings exposure and debug behavior.
- Review dangerous admin bulk actions.
- Review unsafe file handling and path usage.
- Review auth and permission regressions carefully.
- Reject any hardcoded secrets or unsafe subprocess execution.
