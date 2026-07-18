---
name: structured-output-guard
description: "Keep machine-readable artifacts valid and trustworthy."
---
# Structured Output Guard Skill

## Purpose
Keep machine-readable artifacts valid and trustworthy.

## Workflow
1. Validate JSON syntax with `python3 -m json.tool`.
2. Validate artifact shape with `make validate-artifacts RUN_ID=<run-id>`.
3. Repair missing required fields before handoff.
4. Record validation results in the Quality Runner-owned run-scoped `quality.json`.

## Required Artifacts
- `.agent-runs/<run-id>/artifacts/risk.json`
- `.agent-runs/<run-id>/artifacts/quality.json`
- `.agent-runs/<run-id>/artifacts/verdict.json`
- `.agent-runs/<run-id>/audit-log.jsonl`

## Stop Rules
- Do not mark a task complete if required artifacts are malformed.
- If an artifact cannot be repaired safely, record a structured error and route to the owning role; never overwrite another role's artifact.
