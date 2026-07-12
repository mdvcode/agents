# Report

P3.2 adds deterministic dynamic routing and bounded repair loops to the generic agent control plane.

## Changes
- Added `.agent-routing.yaml`, `workflow_route.schema.json`, and `agent_workflow.schema.json`.
- Added an authoritative router that ignores unsafe advisory `next_action` values, enforces required and conditional gates, and computes failure fingerprints.
- Added loop state, role/time/token/repair budgets, router trace events, and approval stops to the role runner and outer workflow runner.
- Added focused tests for malicious transitions, optional gates, publication prerequisites, budget stops, progress detection, and bounded loops.

## Validation
- `make validate-artifacts`: passed.
- `make security`: passed.
- Focused router/loop/runner/workflow tests: 35 passed.
- Full pytest: 127 collected, 126 passed, 1 skipped.
- `git diff --check`: passed.

## Next Action
Review the working-tree diff. No commit, push, PR, merge, or deployment was performed.
