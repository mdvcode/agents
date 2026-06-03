# Workflow Graph

## GitHub Issue Flow
User gives project + GitHub issue -> branch -> `docs/projects/<project>/issues/issue-<number>.md` -> `/goal` -> `artifacts/plan.md` -> risk -> patch -> checks -> review -> report -> verdict -> PR/handoff -> project wiki/memory update.

## Flowfox Approve-To-PR Flow
User gives Flowfox issue -> branch -> private issue journal -> plan/risk -> minimal patch -> focused checks -> local dev server -> screenshot/video/trace evidence -> report/verdict `await_approval` -> user approve -> verify git identity -> exclude control-plane/private files -> stage approved public files only -> commit with no agent/AI wording -> push branch -> create/update sanitized PR with no agent/AI wording -> update private issue journal and audit log.

## Artifact Flow
`plan.md` -> `risk.json` -> implementation -> `quality.json` -> `security.md` -> `review.md` -> `report.md` -> `verdict.json` -> `audit_log.jsonl`.

## Knowledge Flow
Raw source -> project issue journal -> project topic memory -> project wiki page -> future task context.

## Stop Flow
Protected path or high risk -> update risk/verdict -> stop and request approval.
