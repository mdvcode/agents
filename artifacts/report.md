# Report

P4.1 makes project memory retrieval part of every role context manifest.

## Changes
- Added local BM25 ranking over Markdown heading chunks with deterministic ordering and source provenance.
- Scoped target-project retrieval to its private `memory/`, `wiki/`, `graph/`, and `issues/` roots and required `privacy.md`; the agent workspace uses only global system memory.
- Added path, symlink, source-size, chunk-size, result-count, and retrieval-byte controls.
- Added a run-local retrieved context file to the existing executor prompt path and recorded queries, candidates, selected chunks, scores, and status in the manifest.
- Added focused tests for relevance, project isolation, privacy gating, traversal rejection, budgets, global-memory isolation, and manifest integration.
- Documented the retrieval and knowledge flows in durable memory/wiki/graph pages.

## Validation
- Focused tests: 18 passed.
- Retrieval smoke: 18 candidates, 6 bounded sections selected and injected with provenance.
- Full pytest: 133 collected, 132 passed, 1 skipped.
- `make validate-artifacts`, `make security`, `git diff --check`, and `make check`: passed.

## Next Action
Review the working-tree diff. No commit, push, PR, merge, deployment, or external memory service was used.
