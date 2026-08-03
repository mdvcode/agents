# Decision: Evaluation Framework first

Date: 2026-08-03

## Status

Accepted for Milestone 3.

## Context

The Harness already records plans, gates, context provenance, runtime metrics, approvals, loops, and publication state, but had no common contract for proving that a model, prompt, retrieval, loop, or memory change improved outcomes.

## Decision

- Keep benchmark inputs under versioned `evals/` datasets, golden tasks, regressions, rubrics, and benchmark manifests.
- Score authoritative `.agent-runs/<run-id>` evidence without mutating or executing content from the subject run.
- Use one metric dimension per evaluator and expose both normalized score and evidence.
- Treat missing telemetry as unavailable, calculate explicit weighted coverage, and block claims when required evidence or coverage is insufficient.
- Compare only reports with identical frozen dataset and rubric fingerprints.
- Preserve clean-control cases next to known failures to keep false-positive behavior measurable.
- Keep model pricing explicit and rubric-specific; never infer cost from an unknown model.

## Consequences

The Harness can now build local baselines, catch per-metric regressions, and rank compatible variants. The deterministic plane is suitable for CI and for bootstrapping richer Harbor or semantic-judge evals later. Autonomous prompt/model mutation, external trace mining, Docker task execution, and LLM-as-judge remain outside this slice.
