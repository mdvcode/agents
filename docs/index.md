# Agent Workspace Index

This repository is the local operating base for agents working across supported project profiles.

## Start Here
- `docs/onboarding.md`: how a new agent should enter the workspace.
- `docs/agent-system.md`: current agent analysis and improvement plan.
- `docs/git-and-logs.md`: git, logs, docs, and artifact expectations.
- `docs/issues/README.md`: per-GitHub-issue history and branch tracking.
- `docs/projects/README.md`: multi-project private memory layout and privacy rules.
- `docs/wiki/index.md`: curated compounding knowledge.
- `docs/memory/MEMORY.md`: cross-issue long-term memory.
- `docs/graph/README.md`: project maps for files, workflows, risks, and agents.
- `docs/templates/goal.md`: `/goal` structure for non-trivial tasks.

## Kanban Boards
- `docs/kanban/tasks.md`: active tasks and process state.
- `docs/kanban/tests-and-fixes.md`: test failures, fixes, and retest loops.
- `docs/kanban/features.md`: feature ideas and delivery slices.

## Issue History
- `docs/projects/<project>/issues/issue-<number>.md`: durable private timeline for one GitHub issue and its branch.
- `docs/issues/_template.md`: copy this when starting a new project issue journal.

## Projects
- `docs/projects/_template/`: copy this for each new project.
- `docs/projects/<project>/privacy.md`: what can and cannot be published.
- `docs/projects/<project>/issues/`: private issue journals.
- `docs/projects/<project>/memory/`: project-private long-term memory.
- `docs/projects/<project>/wiki/`: project-private curated knowledge.
- `docs/projects/<project>/graph/`: project-private maps.

## Memory And Knowledge
- `docs/wiki/`: stable cross-project agent-system knowledge.
- `docs/memory/`: cross-project agent-system memory, daily logs, scratchpad, and topics.
- `docs/graph/`: cross-project agent-system maps that help agents navigate without broad scans.

## Validation
- `make check`: validate artifacts and diff hygiene.
- `make validate-artifacts`: validate required structured artifacts.
- `make security`: lightweight repository-local scan for obvious secrets, private keys, private paths, and protected staged files.
- `make agent-status`: print git status and current verdict.

## Runtime Artifacts
- `artifacts/plan.md`
- `artifacts/risk.json`
- `artifacts/project_profile.json`
- `artifacts/review.md`
- `artifacts/quality.json`
- `artifacts/security.md`
- `artifacts/verdict.json`
- `artifacts/report.md`
- `artifacts/lessons_learned.md`
- `artifacts/audit_log.jsonl`
