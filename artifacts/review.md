# SUMMARY

P3.1b adds explicit role capability and output-contract metadata for the agent workflow, plus a production Codex CLI executor wrapper.

# CORRECTNESS_FINDINGS

- No blocking correctness finding in the focused review.
- `RoleRequest` now includes `prompt_path`, `output_contract`, `project_profile`, `expected_artifacts`, `allowed_tools`, and `filesystem_access`.
- Context manifests now carry the same role-specific prompt, contract, expected artifact, tool, filesystem, and non-empty project profile metadata.
- Critical role completion now depends on declared expected artifacts rather than planner/risk-only hard-coded checks.
- JSON role artifacts are validated through declared artifact schemas where available.
- The executor wrapper returns structured blocked results instead of tracebacks for bad input, missing command, command failure, timeout, or malformed role output.

# POLICY_VIOLATIONS

None identified.

# KNOWN_LESSON_CONFLICTS

No known lesson conflict identified. Flowfox-specific lessons are not applicable.

# SUGGESTED_PATCH

No additional patch suggested before handoff.

# NOTES

`make check` passed with 93 tests and 1 optional real Codex smoke test skipped.
