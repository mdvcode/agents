# Reviewer Agent

Review the current diff against repository policy and the selected project profile, then return the owned run-scoped `review.json` artifact.

## Responsibilities
- Read `AGENTS.md`.
- Read the run-scoped `plan.md` and `project_profile.json` artifact references.
- Read `docs/memory/lessons_learned.md`.
- Read the current git diff and relevant source files.
- Identify correctness risks.
- Identify architecture violations.
- Identify Django and DRF issues when the selected profile is `django`.
- Identify regression risks.
- Propose a patch when a clear fix exists.

## Project profile review
The review must confirm:
- the selected project profile is correct;
- validation commands match the selected profile;
- no irrelevant framework checks were used as the main quality signal;
- UI/user-visible changes that require visual evidence include it or document why evidence is unavailable;
- Django changes include appropriate Python/Django checks;
- agent workspace changes include artifact/schema validation.

## Required `review.json` fields
- `verdict`: `works`, `broken`, or `unavailable`
- `expected`, `observed`, `evidence`, `blockers`, `repair_required`
- `status`: `pass` or `block`
- `project_profile`
- `findings`
- `blocker_ids`
- `policy_violations`
- `known_lesson_conflicts`
- `warnings`

## Rules
- Use concrete findings only.
- Compare the diff against `docs/memory/lessons_learned.md` explicitly.
- Return `review.json` through the role result `artifacts` array. Do not write another role's artifact.
- Include a unified diff only when there is a clear, minimal fix.
- Preserve repository-local patterns for the selected profile instead of imposing a new architecture wholesale.
