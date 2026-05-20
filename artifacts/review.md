# SUMMARY
- Agent workspace was corrected for multiple projects and private memory.
- Added `docs/projects/` project template and explicit privacy/publication rules.
- No Django application code was changed.

# CORRECTNESS_FINDINGS
- `AGENTS.md` now states `/Users/user/agents` is a private control plane.
- Project-specific issue history is now documented as `docs/projects/<project>/issues/issue-<number>.md`.
- Project-specific memory/wiki/graph are now under `docs/projects/<project>/`.
- Global `docs/wiki`, `docs/memory`, and `docs/graph` are reserved for cross-project agent-system knowledge.
- `docs/projects/_template/privacy.md` defines what can and cannot be published to target project repos.
- Issue work starts only when the user gives project + issue number unless a separate monitor automation is requested.

# DJANGO_DRF_FINDINGS
- Not applicable. No Django or DRF application files were changed.

# ARCHITECTURE_FINDINGS
- The new layout separates private control-plane memory from target project repositories.
- The project template prevents memory from different projects mixing by default.

# POLICY_VIOLATIONS
- None found in the planned diff.

# KNOWN_LESSON_CONFLICTS
- None found. The task does not repeat the existing lessons about model ownership, lifecycle events, runtime dependencies, management command side effects, admin schema compatibility, or sweep harness pitfalls.

# SUGGESTED_PATCH
- None.

# NOTES
- Existing staged `.idea/*` files were user-owned pre-existing changes and were not modified.
