# FlowFox Privacy

- Treat GitHub issue bodies, screenshots, customer/client context, and execution notes as private project memory.
- Do not copy raw issue text, private screenshots, private URLs, secrets, or internal notes into public commits or PRs.
- PR summaries may include sanitized user impact, implementation details, and test evidence.
- Local screenshot, video, and trace evidence is required for approve-ready UI or user-visible issue completion, but must be reviewed for private customer data, secrets, private URLs, and internal notes before any evidence is referenced in a public PR.
- After explicit user approval, commits and PRs may be created from the approved Flowfox branch using the repository's configured git identity and the authenticated GitHub account. Approval does not allow publishing private memory, raw sensitive screenshots, unreviewed issue notes, agent/control-plane files, or text that mentions agents, Codex, AI assistance, or automation.
- Live Sanity backfills require explicit human approval before running with `--write`.
