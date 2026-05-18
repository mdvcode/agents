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
