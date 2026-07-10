# Report

P3.1f removed the obsolete project coupling from the agent control plane.

## Changes
- Removed the obsolete repository record, private project memory, stale screenshots, stale role outputs, and old audit/history entries from root artifacts.
- Replaced the project-specific web profile with `nextjs_web` across policy, profiles, schemas, scripts, prompts, docs, and tests.
- Renamed the project-specific visual evidence contract to generic `visual_evidence`.
- Kept transient untracked `tmp/` and `output/` scratch directories out of repository grep by ignoring them instead of mutating personal scratch files.

## Validation
- Obsolete-name repository grep: 0 matches.
- `make validate-artifacts`: passed.
- `make security`: passed.
- Focused pytest: 92 passed.
- Full pytest: 108 passed, 1 skipped.
- `make check`: passed.
- `git diff --check`: passed.

## Next Action
Proceed to P3.1e local Codex runtime repair or P3.2 only after the runtime smoke gate is healthy.
