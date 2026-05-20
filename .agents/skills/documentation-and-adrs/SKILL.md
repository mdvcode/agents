# Documentation And ADRs Skill

## Purpose
Turn durable decisions into readable project memory.

## Workflow
1. Decide whether the knowledge belongs in an issue journal, memory topic, wiki page, or ADR.
2. Keep project issue-specific details in `docs/projects/<project>/issues/`.
3. Keep repeated project patterns in `docs/projects/<project>/memory/topics/`.
4. Keep stable project concepts and architecture in `docs/projects/<project>/wiki/`.
5. Keep cross-project agent-system concepts in global `docs/wiki/`.
6. Add project decision records under `docs/projects/<project>/wiki/decisions/`.

## Rules
- Do not duplicate long logs.
- Link to evidence instead of copying everything.
- Mark project contradictions in `docs/projects/<project>/wiki/contradictions.md`.
- Sanitize anything that may be published to a target project repository or PR.
