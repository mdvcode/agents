# Workflow Graph

## GitHub Issue Flow
User gives project + GitHub issue -> branch -> `docs/projects/<project>/issues/issue-<number>.md` -> `/goal` -> `artifacts/plan.md` -> risk -> patch -> checks -> review -> report -> verdict -> PR/handoff -> project wiki/memory update.

## Artifact Flow
`plan.md` -> `risk.json` -> implementation -> `quality.json` -> `security.md` -> `review.md` -> `report.md` -> `verdict.json` -> `audit_log.jsonl`.

## Knowledge Flow
Raw source -> project issue journal -> project topic memory -> project wiki page -> future task context.

## Stop Flow
Protected path or high risk -> update risk/verdict -> stop and request approval.
