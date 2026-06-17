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

## Flowfox Publication Handoff
For Flowfox UI or user-visible issue work, Browser QA/local evidence collection happens before Report and Orchestrator. For completed non-HIGH issue work allowed by `.agent-policy.yaml`, Git Workflow automatically commits task-scoped public project files, pushes a public-safe branch, and creates or updates the PR using the configured git identity and authenticated GitHub account. Git Workflow must exclude private control-plane files, must not mention agents, Codex, AI assistance, or automation in branch names or commit/PR text, and must send the PR URL plus local website URL after publication.

## Shared Memory
All agents read `AGENTS.md`, project privacy policy, relevant project issue journal, project wiki/memory, global agent-system wiki/memory, and current artifacts as needed.

## Publication Rule
Agents may use private local memory as context, but should publish only sanitized summaries to target project repositories, PRs, or GitHub issue comments.
