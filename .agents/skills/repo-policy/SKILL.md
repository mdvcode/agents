---
name: repo-policy
description: "Apply repository policy, artifact, risk, and autonomy rules before approving changes."
---
# Repo Policy Skill

## Purpose
Apply repository policy consistently before making or approving changes.

## Workflow
1. Read `AGENTS.md` first.
2. Treat `.agent-policy.yaml` as the source of truth for autonomy, publication, protected paths, and human approval gates.
3. Read `artifacts/lessons_learned.md` before major conclusions.
4. Require updated artifacts and verification before claiming success.
5. Escalate immediately when protected paths or high-risk areas are involved.
