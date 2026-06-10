# FlowFox Privacy

- Treat GitHub issue bodies, screenshots, customer/client context, and execution notes as private project memory.
- Do not copy raw issue text, private screenshots, private URLs, secrets, or internal notes into public commits or PRs.
- PR summaries may include sanitized user impact, implementation details, and test evidence.
- Local screenshot, video, and trace evidence is required before publishing UI or user-visible issue completion, but must be reviewed for private customer data, secrets, private URLs, and internal notes before any evidence is referenced in a public PR.
- Completed LOW/MEDIUM issue work may be committed, pushed, and opened or updated as a PR without a separate user approval, using the repository's configured git identity and the authenticated GitHub account. Automated publication does not allow publishing private memory, raw sensitive screenshots, unreviewed issue notes, agent/control-plane files, or text that mentions agents, Codex, AI assistance, or automation.
- Live Sanity backfills require explicit human approval before running with `--write`.
