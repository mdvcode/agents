# Agent System Analysis

## Current State
- The repository has a good role split: planner, risk classifier, implementation agent, test generator, quality runner, security agent, reviewer, report agent, and orchestrator.
- The skills are clear and useful, especially the Django, DRF, security, git, testing, and lessons policies.
- The main weakness was workspace shape: the prompts existed, but there was no obvious front door for a new agent, no durable docs index, no kanban layer, and `artifacts/` had accumulated stale one-off investigation files.

## Improvements Made
- Added an explicit agent workspace model to `AGENTS.md`.
- Added onboarding docs so any agent can enter the repo in the same order.
- Added a docs index and a git/logs/artifacts policy.
- Added three kanban boards:
  - task and process execution;
  - tests and fixes;
  - features.
- Added a durable per-issue journal convention so every GitHub issue can keep its own timeline while `artifacts/` remains current-task scratch space.
- Cleaned stale runtime artifacts so `artifacts/` returns to its intended purpose: current task artifacts plus lessons and audit log.
- Added the PDF-derived architecture:
  - `docs/wiki/` for LLM Wiki-style durable knowledge.
  - `docs/memory/` for persistent memory, daily notes, scratchpad, and topics.
  - `docs/graph/` for project maps.
  - `docs/templates/goal.md` for `/goal` scoping.
  - `schemas/`, `scripts/validate_artifacts.py`, and `Makefile` for structured output guardrails.
  - New skills for issue intake, context engineering, structured output guard, performance optimization, and documentation/ADRs.

## Recommended Next Improvements
- Add a command that creates branch, issue journal, and kanban card from a GitHub issue number.
- Keep kanban cards short and link them to artifact reports instead of duplicating long logs.
- Consider adding an `agent-state.json` only if machines need to consume board state; markdown is better while humans are the primary reviewers.
- If many GitHub issues are active at once, keep one branch and one `docs/issues/issue-<number>.md` journal per issue.

## Agent Flow
1. Planner writes scope and checks.
2. Risk Classifier sets autonomy gates.
3. Implementation Agent patches narrowly.
4. Test Generator covers changed behavior.
5. Quality Runner records quality checks.
6. Security Agent records security checks.
7. Reviewer compares the diff against policy and lessons.
8. Report Agent writes the human summary.
9. Orchestrator writes the final verdict and next action.

## Codex Executor Smoke Gate
Before adding deterministic routing or bounded repair loops, prove the production Codex executor path locally:

```sh
make codex-preflight
make codex-smoke
```

The smoke must run against a real authenticated Codex CLI. It verifies that the Planner role completes, creates `plan.md` and `project_profile.json`, preserves a clean read-only repository, and records raw JSONL plus token usage.
