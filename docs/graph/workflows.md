# Workflow Graph

## GitHub Issue Flow
User gives project + GitHub issue -> branch -> `docs/projects/<project>/issues/issue-<number>.md` -> one `.agent-runs/<run-id>/` -> plan -> risk -> patch -> tests -> quality -> security -> review -> verdict -> PR/handoff -> project wiki/memory update.

## Target Project Auto-To-PR Flow
User gives a registered project issue -> public-safe branch name -> private issue journal -> plan/risk -> read `.agent-policy.yaml` and `.agent-repositories.yaml` -> minimal patch -> focused checks -> local dev server when relevant -> screenshot/video/trace evidence when required -> report/verdict -> verify git identity -> exclude control-plane/private files -> stage task-scoped public files only -> commit with no agent or AI wording -> push branch -> create/update sanitized PR with no agent or AI wording -> send PR URL and local website URL when relevant -> update private issue journal and audit log.

## Artifact Flow
Within one `.agent-runs/<run-id>/`: `plan.md` -> `risk.json` -> implementation -> tests -> `quality.json` -> `security.json` -> `review.json` -> `verdict.json` -> `change_set.json` + `publication_payload.json` -> `publication.json` -> `audit-log.jsonl`.

## Concurrent Task Flow

Task enqueue -> SQLite lease -> worker heartbeat -> Task Intake creates worktree -> authoritative router -> implementation and bounded repair loops -> independent verification plane -> publication from the same worktree or compact exception -> terminal queue status.

Approval required -> run-scoped request and checkpoint fingerprint -> exact-scope human decision -> consume once -> queue existing run id -> resume same worktree/checkpoint -> continue deterministic gates.

Worker process dies -> heartbeat stops -> lease expires -> task requeued with existing run id -> replacement worker detects running/resuming workflow -> `--resume` from checkpoint.

GitHub Actions failure -> HMAC-verified webhook -> governed failed-log read -> secret redaction -> run-scoped CI feedback -> existing run/branch queued at CI repair -> quality and publication update the existing PR.

The queue coordinates tasks; it never replaces `.agent-runs/<run-id>/` as the authoritative state of a task.

## Deterministic Gate Flow

HIGH risk -> approval; CRITICAL security -> blocked; MEDIUM/HIGH security -> approval; UI changed -> frontend verifier; quality/review/CI/frontend broken -> bounded repair; repeated failure plus unchanged diff -> approval; all required gates valid -> publication. Model `next_action` is advisory throughout.

Issue Intake is a deterministic harness stage (`llm_invocation=false`), not an LLM role. It records task/worktree identity before any model-backed role runs.

## Knowledge Flow
Raw source -> project issue journal -> project topic memory -> project wiki page -> future task context.

## Memory Retrieval Flow
Task goal + role -> select the active project's approved private memory roots -> Markdown heading chunks -> local BM25 ranking -> bounded run-local retrieval file -> context manifest -> role generation.

## Stop Flow
Protected path or high risk -> update risk/verdict -> stop and request approval.
