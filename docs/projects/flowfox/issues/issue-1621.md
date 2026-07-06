# Issue 1621: Landing Page Workspace entrypoints

## Links
- GitHub issue: https://github.com/PrimestSpec/flowfox/issues/1621
- Branch: feat/landing-page-workspace-migrate-entrypoints-and-retire-landing-page-generation-modal
- Pull request: https://github.com/PrimestSpec/flowfox/pull/1702
- Related docs: /Users/user/agents/docs/projects/flowfox/AGENTS.md

## Status
- State: review
- Risk: medium
- Owner/agent: codex
- Last updated: 2026-06-29 14:49 CEST

## Goal
- Move Landing Page create/edit entrypoints to `/landing-page-workspace` and retire direct use of the legacy generation modal.

## Goal Structure
### GOAL
- Campaign, global list, detail, and quick-action Landing Page entrypoints open the Landing Page Workspace route with the right campaign/doc query parameters.

### CONTEXT
- The workspace editor and draft APIs already exist, but the dashboard route `/landing-page-workspace` is missing in the current checkout.
- Existing workspace publish patches an existing Sanity landing page and needs create-mode support for drafts without a Sanity document.

### CONSTRAINTS
- Flowfox profile: Next.js / React / Prisma / Sanity / Bun.
- Do not touch migrations, production credentials, protected paths, or private control-plane files in the Flowfox repo.
- Publication must go through `/Users/user/agents/scripts/publish_pr.py`.

### PRIORITY
- Primary: no create/edit dead ends to the legacy Landing Page generation modal.
- Secondary: preserve detail-page config panels and existing table actions.
- Non-goals: broad workspace redesign, auth model changes, migrations, deployment.

### DONE WHEN
- Create buttons deep-link to `/landing-page-workspace?campaignId=<id>&tab=create`.
- Detail workspace CTA deep-links to `/landing-page-workspace?campaignId=<id>&docId=<docId>`.
- Legacy modal imports/usages are removed from affected entrypoints.
- Existing permissions remain enforced.
- Checks and frontend evidence/warnings are recorded.

### VERIFY
- Focused tests or test guidance.
- `bun node_modules/typescript/lib/tsc.js --noEmit`.
- `git diff HEAD --check`.
- Security grep/check over changed files.
- Local browser evidence or authenticated-route blocker screenshot.

### OUTPUT
- Flowfox code patch, private artifacts, and PR if risk remains medium and publication dry-run allows it.

### STOP RULES
- Stop before commit/push/PR if risk becomes high or protected paths are touched.

## Scope
- In: dashboard route, workspace editor/publish create-mode support, entrypoint URL migration, focused tests.
- Out: migrations, production access, merge/deploy, unrelated UI refactors.

## Plan
1. Add shared URL helper for Landing Page Workspace links.
2. Add `/landing-page-workspace` dashboard page/content.
3. Support publish for drafts without `landingPageDocId`.
4. Replace campaign/list/detail/quick-action modal entrypoints with links/navigation.
5. Add focused tests for URL helper and publish create-mode if practical.
6. Run checks, review, evidence, and publication gate.

## Timeline
- 2026-06-29 14:49 CEST: Created issue journal after reading Flowfox policy, project profile, privacy, lessons, and GitHub issue.
- 2026-06-29 15:04 CEST: Implemented route, entrypoint migration, create-mode publish support, focused tests, and local checks.

## Checkpoints
- [x] Issue understood
- [x] Branch ready
- [x] Relevant files inspected
- [x] Risk classified
- [x] Patch ready
- [x] Focused checks run
- [x] Quality/security checks run or blockers recorded
- [x] Review complete
- [x] PR/handoff ready
- [ ] Durable knowledge promoted to wiki/memory if needed

## Trace
| Time | Agent | Action | Evidence |
| --- | --- | --- | --- |
| 2026-06-29 14:49 CEST | codex | Created issue journal | This file |
| 2026-06-29 15:04 CEST | codex | Ran focused tests and typecheck | `artifacts/quality.json` |
| 2026-06-29 15:15 CEST | codex | Closed accidental PR #1701 and published draft PR #1702 from the provided branch | `artifacts/publication.json` |

## Decisions
- Use the provided working branch `feat/landing-page-workspace-migrate-entrypoints-and-retire-landing-page-generation-modal`; accidental extra branch PR #1701 was closed and its branch deleted.
- Keep `external/agents` worktree change excluded as unrelated/protected.

## Checks
- Passed: artifact validation; focused Vitest; TypeScript; diff whitespace; focused security grep; commit/push hooks with bundled Node.
- Failed or blocked: authenticated frontend evidence blocked by sign-in redirect.

## Files Changed
- Flowfox code patch in dashboard workspace route, Landing Page entrypoints, workspace publish/editor path, CMS helper, and focused tests.

## Blockers
- Authenticated local workspace visual evidence unavailable in current in-app browser session.

## Final Summary
- Draft PR #1702 is open from the provided working branch.

## Next Action
- Authenticated frontend QA pass before marking PR ready.
