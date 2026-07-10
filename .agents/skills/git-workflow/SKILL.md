---
name: git-workflow
description: "Keep git branch, staging, commit, push, and PR publication safe and reviewable."
---
# Git Workflow Skill

## Purpose
Keep autonomous git operations small, reviewable, and safe.

## Rules
- Branch names should follow the repository or user convention for feature work and should never target `main` or `master` directly for autonomous commits.
- Commits should be small and reviewable.
- PR bodies must summarize scope, risk, checks, blockers, and next actions.
- Commit and push only when the user explicitly asks for it or `.agent-policy.yaml` allows it for the target project and risk class.
- Do not auto-commit or auto-push high-risk work.
- Never stage protected paths silently.

## Autonomous PR Publishing for non-HIGH risk tasks
- Read `.agent-policy.yaml` and `.agent-repositories.yaml` before deciding whether work can be committed, pushed, or opened as a PR.
- Before publishing UI or user-visible work that requires visual evidence, capture local-site evidence: screenshots for static states and video or trace evidence for interaction-heavy flows.
- For completed LOW/MEDIUM work, publish without a separate user approval when the active repository registry and project policy allow it, checks pass, no protected paths are touched, no blockers remain, and required local evidence is provided or the policy allows a draft PR without it.
- Before committing, re-check `git status --short`, stage only the task-scoped changed/added/deleted public project files, and do not include unrelated working-tree changes.
- Never stage or push private control-plane paths for a target project, including `/Users/user/agents`, `external/agents/`, `.agents/`, `artifacts/`, private issue journals, private memory/wiki/graph files, prompt files, skills, audit logs, or sensitive screenshot/video/trace artifacts.
- Use `git config user.name` and `git config user.email` from the target repository for commits. Do not set or overwrite identity unless the user explicitly instructs you to do so.
- Push the current issue branch to the user's configured GitHub remote after the policy-allowed commit succeeds.
- Create or update the PR with the authenticated GitHub account already available to `git`/`gh`; include sanitized product/engineering scope, checks, risk, blockers, and safe local evidence references.
- Do not mention agents, Codex, AI assistance, automation, private control-plane files, `.agents`, `artifacts`, or `external/agents` in commit messages, PR titles, PR bodies, issue comments, or release notes.
- Never auto-merge or deploy.
