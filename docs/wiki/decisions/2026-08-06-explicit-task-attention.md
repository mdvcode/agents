# Explicit Task Attention And Run-Bound Answers

Date: 2026-08-06

## Decision

A background task that cannot continue autonomously must enter explicit attention state. The workflow records a concise summary, concrete missing items, current role, and action class. The worker preserves that detail in the queue instead of reducing it to generic `blocked` or `approval required` text.

`agent status` and `agent watch` are the user-facing attention surfaces. `agent watch` follows state transitions and exits when the task completes or needs action. Missing information is supplied with `agent answer <run-id> "..."`; the sanitized response stays in the private run directory, the exact pending continuation gate is consumed once, and the same checkpoint resumes with prior answers included in the role prompt.

When a question has a small closed answer set, the role may attach 2-3 structured options with a recommendation and concise tradeoff. The dashboard renders them as a select control and retains a custom-answer path. Text-only questions and older `answer_or_approve` records remain compatible.

Information and authority remain separate. `agent answer` is accepted only for attention explicitly classified as a role question. The user-facing status does not suggest approval as a substitute for missing information. HIGH risk, security, protected-path, publication, and other decision gates remain `approve` and require `agent approve`.

## Retry rule

When a child workflow has already selected approval, blocked, retry, repair, resume, dead letter, or failure state, the outer step runner stops immediately instead of repeating the same command. Existing repair loops continue to compare failure and diff fingerprints and stop on a repeated no-progress result.

Answered questions are fingerprinted by role plus stable question identity. If the same role asks the same question after its answer was recorded, the workflow does not create another answer or approval gate. It enters a visible technical blocker that identifies the repeated question and directs the operator to inspect the role prompt or task context. A `blocked` or `failed` role result is never classified as an informational question; only an explicit `awaiting_approval` role result may accept `agent answer`.

## Consequences

- Questions and prerequisites are visible without reading worker logs or raw transcripts.
- User input resumes the authoritative run instead of creating a duplicate task.
- Empty blockers receive a deterministic fallback summary.
- Answers are bounded and sanitized, but users are still instructed never to provide secrets.
- Background execution remains asynchronous; `agent watch` is the explicit live terminal surface.
