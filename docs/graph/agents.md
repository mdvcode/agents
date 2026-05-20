# Agent Graph

## Roles
- Planner -> creates scope and checks.
- Risk Classifier -> sets risk and autonomy.
- Implementation Agent -> patches narrowly.
- Test Generator -> ensures behavior coverage.
- Quality Runner -> records quality checks.
- Security Agent -> records security checks.
- Reviewer -> finds regressions and policy violations.
- Report Agent -> writes human summary.
- Orchestrator -> writes verdict and next action.

## Handoffs
Planner -> Risk -> Implementation -> Tests -> Quality -> Security -> Review -> Report -> Orchestrator.

## Shared Memory
All agents read `AGENTS.md`, project privacy policy, relevant project issue journal, project wiki/memory, global agent-system wiki/memory, and current artifacts as needed.

## Publication Rule
Agents may use private local memory as context, but should publish only sanitized summaries to target project repositories, PRs, or GitHub issue comments.
