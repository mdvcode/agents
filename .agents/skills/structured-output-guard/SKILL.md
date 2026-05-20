# Structured Output Guard Skill

## Purpose
Keep machine-readable artifacts valid and trustworthy.

## Workflow
1. Validate JSON syntax with `python3 -m json.tool`.
2. Validate artifact shape with `make validate-artifacts`.
3. Repair missing required fields before handoff.
4. Record validation results in `artifacts/quality.json`.

## Required Artifacts
- `artifacts/risk.json`
- `artifacts/quality.json`
- `artifacts/verdict.json`
- `artifacts/audit_log.jsonl`

## Stop Rules
- Do not mark a task complete if required artifacts are malformed.
- If an artifact cannot be repaired safely, record the blocker in `artifacts/verdict.json`.
