# Decision: PDF-Driven Agent System Upgrade

## Date
2026-05-19

## Context
The user provided `AI links.pdf`, which collected ideas about production agent skills, persistent memory, LLM Wiki, graph memory, structured output validation, managed-agent checkpoints, and token optimization.

## Decision
Extend the local agent workspace with:
- LLM Wiki-style durable knowledge in `docs/wiki/`.
- Persistent memory in `docs/memory/`.
- Project graph maps in `docs/graph/`.
- A `/goal` template in `docs/templates/goal.md`.
- Artifact schemas and local validation.
- New skills for issue intake, context engineering, structured output guards, performance optimization, and documentation/ADRs.

## Consequences
- Future agents have a clearer route into the workspace.
- GitHub issues can accumulate durable knowledge instead of only producing temporary artifacts.
- Structured artifacts can be verified locally.
- The workspace stays plain-text and git-friendly.
