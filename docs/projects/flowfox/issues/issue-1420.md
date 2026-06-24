# Issue 1420: Align Settings card CTA style

## Links
- GitHub issue: https://github.com/PrimestSpec/flowfox/issues/1420
- Branch: `issue/1420`
- Pull request: https://github.com/PrimestSpec/flowfox/pull/1426
- Related docs: `/Users/user/agents/docs/projects/flowfox/AGENTS.md`

## Status
- State: review
- Risk: low
- Owner/agent: codex
- Last updated: 2026-06-22 15:26 CEST

## Goal
- Align Settings overview card action buttons on `/settings` with the gray/dark CTA style.

## Goal Structure
### GOAL
- Change only Settings overview card button variants on `app/(dashboard)/settings/page.tsx`.

### CONTEXT
- The dashboard `Button` maps `primary` to blue and `outline` / `secondary` to gray styles.
- The roles card already uses a gray outline CTA on the same page.

### CONSTRAINTS
- Do not change global button theme, permissions, routes, auth logic, or primary buttons on other pages.
- Do not publish private issue screenshots or raw issue text.

### PRIORITY
- Primary: make all Settings overview card CTAs visually match gray card CTA style.
- Secondary: preserve links and permission wrappers.
- Non-goals: global theme changes, route changes, broad redesign.

### DONE WHEN
- Settings overview card CTAs no longer use `variant="primary"` and the page verifies locally or evidence warnings are recorded.

### VERIFY
- TypeScript check, diff whitespace check, changed-file security review, browser evidence for `/settings` when available.

### OUTPUT
- Flowfox code patch, updated private artifacts, sanitized PR if LOW risk publication gates pass.

### STOP RULES
- Stop before commit/push/PR if the patch touches protected paths or escalates to HIGH risk.

## Scope
- In: `app/(dashboard)/settings/page.tsx`
- Out: `app/theme.ts`, shared `Button`, permissions, routes, credentials, migrations

## Plan
1. Update Settings overview card CTA variants to gray outline.
2. Run selected Flowfox checks.
3. Collect local frontend evidence or record blocker/warning.
4. Run mandatory publication dry-run and publish if allowed.

## Timeline
- 2026-06-22 15:08 CEST: Created issue journal and classified as low risk.

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
- [x] Durable knowledge promoted to wiki/memory if needed

## Trace
| Time | Agent | Action | Evidence |
| --- | --- | --- | --- |
| 2026-06-22 15:08 CEST | codex | Created issue journal | This file |
| 2026-06-22 15:08 CEST | codex | Read issue and confirmed target buttons | `gh issue view 1420`; `rg` in settings page |
| 2026-06-22 15:24 CEST | codex | Applied patch and checks | `git diff`; TypeScript; `git diff --check` |
| 2026-06-22 15:24 CEST | codex | Captured local frontend blocker evidence | `/private/tmp/flowfox-issue-1420-settings-auth-blocker.png` |
| 2026-06-22 15:26 CEST | codex | Published draft PR | https://github.com/PrimestSpec/flowfox/pull/1426 |
| 2026-06-22 15:35 CEST | codex | Extended patch after local screenshot showed remaining blue CTAs | `rg` confirmed no `variant="primary"` remains in settings page |

## Decisions
- Use `variant="outline"` for the six listed CTAs because the page already uses an outline gray CTA in the roles card.
- The roles card in the current branch already uses `variant="outline"` and label `Konfigurieren`; no code change was needed there.
- After local review, all other card CTAs on `/settings` were also aligned to `outline` to satisfy the done criterion.

## Checks
- Pending:
- Passed: `bun node_modules/typescript/lib/tsc.js --noEmit`; `git diff HEAD --check`; `rg` confirmed no `variant="primary"` remains in the settings page; changed-diff secret/env marker scan; mandatory publication dry-run; publication executor selected-file security scan
- Failed or blocked: full authenticated `/settings` visual evidence blocked by unauthenticated redirect plus Turbopack sign-in runtime error

## Files Changed
- `app/(dashboard)/settings/page.tsx`

## Blockers
- Full local visual verification of the authenticated settings page is unavailable in the current browser session.
- Publication executor worktree lacks `node_modules`, so its internal profile typecheck command warned with module-not-found; root repository typecheck passed before publication.

## Final Summary
- Updated all Settings overview card CTAs to `outline`; roles CTA was already outline.
- Draft PR created: https://github.com/PrimestSpec/flowfox/pull/1426
- Commit: `bcba29e159cce2af492d37f073c62eee8af863e0`

## Next Action
- Human review and authenticated visual check of `/settings`; mark PR ready after visual evidence is confirmed.
