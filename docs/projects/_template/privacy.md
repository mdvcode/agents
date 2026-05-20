# Project Privacy Policy

## Classification
- Privacy level: private
- Default publication rule: private memory stays in `/Users/user/agents`.

## Allowed To Commit To Target Project
- Code changes requested by the issue.
- Tests.
- Public developer documentation approved by the user.
- Sanitized changelog or PR notes.

## Not Allowed To Commit By Default
- Issue journals.
- Agent trace.
- Raw artifacts.
- Internal reasoning summaries.
- Customer data.
- Secrets, tokens, keys, credentials.
- Private URLs or internal infrastructure details.
- Screenshots or logs containing private data.

## PR Summary Rules
- Summarize behavior, tests, and risk.
- Do not include private issue text unless explicitly approved.
- Do not include raw execution trace.
- Remove names, emails, tokens, private URLs, and customer-identifying details unless necessary and approved.

## Memory Rules
- Store private issue history in `issues/`.
- Store reusable project knowledge in `memory/` or `wiki/`.
- Store cross-project agent-system knowledge only in global `docs/wiki/` or `docs/memory/`.
