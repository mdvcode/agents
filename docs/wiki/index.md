# Agent Wiki

This is the compounding knowledge layer for the agent workspace.

Unlike `artifacts/`, this directory is not cleared between tasks. Agents update it when a task produces durable knowledge that should improve future work.

## Pages
- `concepts/agent-memory.md`: memory architecture for this workspace.
- `concepts/goal-prompt.md`: `/goal` structure for scoping tasks.
- `concepts/llm-wiki.md`: persistent wiki pattern from the PDF.
- `concepts/structured-output-guard.md`: schema and repair expectations for agent outputs.
- `concepts/token-hygiene.md`: how agents avoid wasting context.
- `entities/agent-workspace.md`: map of this local repository.
- `decisions/2026-05-19-pdf-agent-system-upgrade.md`: decision record for the PDF-driven upgrade.
- `contradictions.md`: claims or practices that need reconciliation.

## Update Rules
- Add durable knowledge here, not raw logs.
- Prefer short pages with links to source issue journals.
- Mark contradictions instead of silently overwriting older claims.
- Keep raw task evidence in `docs/issues/` or `artifacts/`, then summarize it here.
