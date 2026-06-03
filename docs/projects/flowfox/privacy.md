# Flowfox Privacy Policy

## Classification
- Privacy level: private
- Default publication rule: private memory stays in `external/agents`.

## Allowed To Commit To Target Project
- Issue-requested code changes.
- Tests.
- Public developer documentation approved by the user.
- Sanitized PR or changelog notes.

## Not Allowed To Commit By Default
- Issue journals.
- Agent trace.
- Raw artifacts.
- Internal reasoning summaries.
- Customer data.
- Secrets, tokens, keys, credentials.
- Private URLs or internal infrastructure details.

## PR Summary Rules
- Summarize behavior, tests, and risk.
- Do not include raw execution trace.
- Remove names, emails, tokens, private URLs, and customer-identifying details unless necessary and approved.

## Memory Rules
- Store private issue history in `issues/`.
- Store reusable project knowledge in `memory/` or `wiki/`.
