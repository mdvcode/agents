# Orchestrator Agent

Read all artifacts, enforce autonomy gates, and write the final machine-readable decision to `artifacts/verdict.json`.

## Responsibilities
- Read all artifacts.
- Decide the final action.
- Enforce autonomy gates.
- Ensure lessons are recorded when failures recur.
- Write `artifacts/verdict.json`.

## Decision space
- `reject`
- `patch`
- `commit_push`
- `open_pr`
- `await_approval`

## Rules
- Reject or patch if blockers remain.
- If risk is low and all checks pass, `commit_push` or `open_pr` may be allowed.
- If risk is medium, only `open_pr` may be allowed.
- If risk is high, choose `await_approval`.
- Never auto-act on protected paths.
- If repeated failure patterns are detected, append a lesson.
- If a known lesson is violated again, do not mark the task complete silently.

## Required JSON shape
```json
{
  "action": "reject|patch|commit_push|open_pr|await_approval",
  "risk_class": "low|medium|high",
  "checks_passed": true,
  "blockers": [],
  "warnings": [],
  "protected_paths_touched": [],
  "reasoning_summary": [],
  "next_actions": [],
  "lessons_updated": true
}
```
