# Long-Term Agent Memory

Durable facts that should survive across issues:

- `artifacts/` is the active workbench and may be rewritten between issues.
- `docs/` is permanent private control-plane memory and should be updated, not cleared.
- Multiple target projects are supported under `docs/projects/<project>/`.
- Every GitHub issue should have one branch and one `docs/projects/<project>/issues/issue-<number>.md` journal.
- Stable project knowledge belongs in `docs/projects/<project>/wiki/` or `docs/projects/<project>/memory/topics/`.
- Stable cross-project agent-system knowledge belongs in global `docs/wiki/` or `docs/memory/topics/`.
- Private project memory is not published to target project GitHub repositories by default.
- Use `make check` before claiming this agent workspace is verified.
