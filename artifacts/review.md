# SUMMARY
- Agent workspace structure was improved with durable docs, onboarding, and kanban boards.
- Stale one-off artifacts were removed from `artifacts/`; required artifacts, lessons, and audit log were preserved.
- No Django application code was changed.

# CORRECTNESS_FINDINGS
- `AGENTS.md` now points new agents to onboarding, docs, git/logs, and kanban boards.
- The docs match the screenshot request: git history, docs store with logs, kanban for tasks/process, kanban for tests/fixes, kanban for features, and onboarding.
- The docs now also support the clarified GitHub issue workflow: one issue branch plus one durable issue journal under `docs/issues/`.
- Artifact cleanup keeps the required artifact set listed in `AGENTS.md`.

# DJANGO_DRF_FINDINGS
- Not applicable. No Django or DRF application files were changed.

# ARCHITECTURE_FINDINGS
- The agent workflow is now explicit enough for another agent to enter the repository without relying on hidden context.
- Long-lived process knowledge now lives in `docs/`; transient task state remains in `artifacts/`.

# POLICY_VIOLATIONS
- None found in the planned diff.

# KNOWN_LESSON_CONFLICTS
- None found. The task does not repeat the existing lessons about model ownership, lifecycle events, runtime dependencies, management command side effects, admin schema compatibility, or sweep harness pitfalls.

# SUGGESTED_PATCH
- None.

# NOTES
- Existing staged `.idea/*` files were user-owned pre-existing changes and were not modified.
