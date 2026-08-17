# Deterministic Model And Workflow Policy

Date: 2026-08-17

## Decision

The Harness selects a configured execution profile in local deterministic code. It does not use Model Router and does not allow a role request to override the selected model, reasoning effort, or service tier.

- `complex`: `gpt-5.6-sol`, high reasoning, Fast service for complex or high-risk implementation, risk-bearing or large review, optional deep verifiers, and escalation after an actual failure or repeated repair;
- `balanced`: `gpt-5.6-terra`, medium reasoning, Fast service for ordinary local model-backed work;
- `economy`: `gpt-5.6-luna`, low reasoning, Fast service for mechanical classification, reporting, formatting-like implementation, structured-output repair, and a first narrow implementation repair.

This follows the official model guidance: [Sol is the flagship for complex reasoning and coding, Terra balances intelligence and cost, and Luna is optimized for cost-sensitive high-volume work](https://developers.openai.com/api/docs/models).

A resume after user input is continuity, not a failed attempt, and therefore does not trigger escalation. Each selected profile, its reason, and its escalation level are written into the role request, workflow trace, runtime progress, and role result.

## Conditional workflow

No roles are added. The existing roles are activated from changed-file impact and risk:

- test generation runs only when code changed;
- frontend QA runs only for user-visible/UI impact;
- architecture consistency runs for structural files or a large change;
- semantic conflict review runs for medium/high-risk domain code;
- reviewer uses a model for code, UI, medium/high risk, or a large change and otherwise produces a deterministic evidence summary;
- quality, security, issue intake, and orchestration remain deterministic Harness stages.

## Missing-requirement lifecycle

An answerable question carries a normalized semantic requirement. The run stores both requested and closed requirements. Only one user-visible request is allowed for a matching requirement. A repeated or semantically equivalent request is converted to a structured technical failure with the matched requirement id and diagnostics. The answer is private run-bound input and resumes the same role checkpoint, SDK process, and SDK thread.

## Consequences

- Routine work no longer pays the latency and token cost of Sol/high or unnecessary LLM gates.
- Complex and failed work still has a deterministic path to Sol/high.
- Profile overrides fail closed instead of silently drifting from policy.
- Workflow depth is explainable from risk, changed paths, and changed size.
- Rewording a previously requested requirement cannot create a user-facing question loop.
