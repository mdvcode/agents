# Decision: Gate production changes with a frozen deterministic corpus

Date: 2026-08-03

## Status

Accepted for Milestone E2.

## Context

The first Evaluation Framework could score and compare authoritative runs, but one clean-control dataset could not establish safety or prevent regressions across risk routing, publication, context, repair, and approval behavior.

## Decision

- Keep version-1 run scorecards for real model, prompt, retrieval, loop, and memory experiments.
- Add version-2 sanitized contract datasets for deterministic CI evaluation without invoking a model.
- Freeze task, expectations, tags, categories, and critical metrics; exclude observed candidate evidence from compatibility fingerprints.
- Freeze a separate versioned scorer-contract fingerprint so reports produced with different deterministic rubric semantics cannot be compared.
- Require exact case identity and dataset fingerprints against a reviewed baseline.
- Make security, publication safety, and risk routing non-compensating metrics with a required score of `1.0`.
- Reject executable fields in corpus inputs and keep evaluation read-only.
- Promote a behavior only with both a clean/golden case and a reviewed negative mutation.

## Consequences

CI can now block contract regressions with deterministic evidence while real run scorecards continue measuring latency, tokens, cost coverage, and interventions for actual variants. The checked-in corpus is sanitized contract evidence rather than a claim that synthetic snapshots are production outcomes. Model execution, LLM judges, and private historical traces remain outside this gate.
