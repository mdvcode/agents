# Git Workflow Skill

## Purpose
Keep autonomous git operations small, reviewable, and safe.

## Rules
- Branch names should follow the repository or user convention for feature work and should never target `main` or `master` directly for autonomous commits.
- Commits should be small and reviewable.
- PR bodies must summarize scope, risk, checks, blockers, and next actions.
- Commit only when the user explicitly asks for it.
- Push only when the user explicitly asks for it.
- Do not auto-commit or auto-push high-risk work.
- Never stage protected paths silently.

## Flowfox Approval Publishing
- Before asking for approval on Flowfox UI or user-visible work, capture local-site evidence: screenshots for static states and video or trace evidence for interaction-heavy flows.
- Treat user approval as approval of the exact reviewed state. After approval, re-check `git status --short`, stage only the approved changed/added/deleted files, and do not include unrelated working-tree changes.
- Never stage or push private control-plane paths for Flowfox, including `/Users/user/agents`, `external/agents/`, `.agents/`, `artifacts/`, private issue journals, private memory/wiki/graph files, prompt files, skills, audit logs, or sensitive screenshot/video/trace artifacts.
- Use `git config user.name` and `git config user.email` from the target repository for commits. Do not set or overwrite identity unless the user explicitly instructs you to do so.
- Push the current issue branch to the user's configured GitHub remote after the approved commit succeeds.
- Create or update the PR with the authenticated GitHub account already available to `git`/`gh`; include sanitized product/engineering scope, checks, risk, blockers, and safe local evidence references.
- Do not mention agents, Codex, AI assistance, automation, private control-plane files, `.agents`, `artifacts`, or `external/agents` in commit messages, PR titles, PR bodies, issue comments, or release notes.
- Never auto-merge or deploy from this approval.
