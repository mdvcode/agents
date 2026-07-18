# Workflow Graph

## GitHub Issue Flow
User gives project + GitHub issue -> branch -> `docs/projects/<project>/issues/issue-<number>.md` -> one `.agent-runs/<run-id>/` -> plan -> risk -> patch -> tests -> quality -> security -> review -> verdict -> PR/handoff -> project wiki/memory update.

## Target Project Auto-To-PR Flow
User gives a registered project issue -> public-safe branch name -> private issue journal -> plan/risk -> read `.agent-policy.yaml` and `.agent-repositories.yaml` -> minimal patch -> focused checks -> local dev server when relevant -> screenshot/video/trace evidence when required -> report/verdict -> verify git identity -> exclude control-plane/private files -> stage task-scoped public files only -> commit with no agent or AI wording -> push branch -> create/update sanitized PR with no agent or AI wording -> send PR URL and local website URL when relevant -> update private issue journal and audit log.

## Artifact Flow
Within one `.agent-runs/<run-id>/`: `plan.md` -> `risk.json` -> implementation -> tests -> `quality.json` -> `security.json` -> `review.json` -> `verdict.json` -> `change_set.json` + `publication_payload.json` -> `publication.json` -> `audit-log.jsonl`.

## Knowledge Flow
Raw source -> project issue journal -> project topic memory -> project wiki page -> future task context.

## Memory Retrieval Flow
Task goal + role -> select the active project's approved private memory roots -> Markdown heading chunks -> local BM25 ranking -> bounded run-local retrieval file -> context manifest -> role generation.

## Stop Flow
Protected path or high risk -> update risk/verdict -> stop and request approval.
