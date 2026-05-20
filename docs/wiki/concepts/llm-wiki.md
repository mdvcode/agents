# LLM Wiki Pattern

The PDF describes the LLM Wiki idea: do not re-discover the same knowledge from raw material on every task. Instead, compile durable knowledge once into a readable wiki and keep it current.

## Workspace Interpretation
- Raw sources: PDFs, GitHub issues, PR discussions, logs, failing checks.
- Active workbench: `artifacts/`.
- Durable project issue history: `docs/projects/<project>/issues/`.
- Project-private curated wiki: `docs/projects/<project>/wiki/`.
- Project-private cross-issue memory: `docs/projects/<project>/memory/`.
- Global agent-system wiki: `docs/wiki/`.

## Ingest Loop
1. Read a source.
2. Summarize what is useful.
3. Update the relevant project issue journal.
4. Update project topic memory if the knowledge will recur.
5. Update project wiki pages for stable concepts or entities.
6. Add contradictions to `docs/wiki/contradictions.md`.

## Query Loop
1. Search exact terms with `rg`.
2. Read relevant wiki and memory pages.
3. Read raw sources only when evidence or exact wording matters.
