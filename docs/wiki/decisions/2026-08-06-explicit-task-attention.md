# Explicit Task Attention And Run-Bound Answers

Date: 2026-08-06

## Decision

A background task that cannot continue autonomously must enter explicit attention state. The workflow records a concise summary, concrete missing items, current role, and action class. The worker preserves that detail in the queue instead of reducing it to generic `blocked` or `approval required` text.

`agent status` and `agent watch` are the user-facing attention surfaces. `agent watch` follows state transitions and exits when the task completes or needs action. Missing information is supplied with `agent answer <run-id> "..."`; the sanitized response stays in the private run directory, the exact pending continuation gate is consumed once, and the same checkpoint resumes with prior answers included in the role prompt.

Information and authority remain separate. `agent answer` is accepted only for attention explicitly classified as a role question. The user-facing status does not suggest approval as a substitute for missing information. HIGH risk, security, protected-path, publication, and other decision gates remain `approve` and require `agent approve`.

## Retry rule

When a child workflow has already selected approval, blocked, retry, repair, resume, dead letter, or failure state, the outer step runner stops immediately instead of repeating the same command. Existing repair loops continue to compare failure and diff fingerprints and stop on a repeated no-progress result.

## Consequences

- Questions and prerequisites are visible without reading worker logs or raw transcripts.
- User input resumes the authoritative run instead of creating a duplicate task.
- Empty blockers receive a deterministic fallback summary.
- Answers are bounded and sanitized, but users are still instructed never to provide secrets.
- Background execution remains asynchronous; `agent watch` is the explicit live terminal surface.
