# Issue 816: Campaign Usage Tracking Hover Popover

## Summary
- The campaign detail Volumen-Abgleich KPI popover closed before users could move the pointer into it and click the Usage Tracking CTA.
- Root cause: the popover is rendered through a React portal under `document.body`, while hover state was controlled only by the KPI card wrapper. Leaving the card immediately hid the portal.

## Implementation
- Added popover-side mouse enter/leave handlers.
- Split open and close timers to allow delayed close during pointer transit.
- Cleared stale timers when re-entering either the card or the portal.
- Added transparent portal top padding so the pointer path from the card to the visible popover has no dead gap.
- Added a jsdom regression test for card-to-popover movement, CTA click, and clean close after leaving.

## Verification
- `bun test components/campaigns/detail/__tests__/CampaignKpiBar.test.tsx`: passed as a runner check with the jsdom test skipped in Bun's non-DOM environment.
- `bunx vitest run components/campaigns/detail/__tests__/CampaignKpiBar.test.tsx`: blocked by local Rollup optional native package code-signature error.
- `bun run typecheck`: blocked by unrelated existing Prisma/user typing errors.
- `git diff --check`: passed.

## Notes
- No backend or data model changes were needed.
- `external/` was not present in this checkout; agent context came from `agents/`.

## PR Commit Signatures
- `components/campaigns/detail/CampaignKpiBar.tsx`: `fix(campaigns): keep usage tracking popover hoverable`
- `components/campaigns/detail/__tests__/CampaignKpiBar.test.tsx`: `test(campaigns): cover usage tracking popover hover persistence`
- `agents/AGENTS.md`: `docs(agents): capture FlowFox portal hover guidance`
- `agents/docs/projects/flowfox/issues/issue-816.md`: `docs(agents): journal FlowFox issue 816`
- `agents/artifacts/*`: `docs(agents): refresh issue 816 execution artifacts`

## Sanitized PR Text
### Summary
- Keeps the campaign Volumen-Abgleich popover open while moving from the KPI card into the Usage Tracking dropdown.
- Adds portal-side hover handling plus a short close delay so the CTA remains clickable.
- Adds regression coverage for the hover transition and CTA click.

### Verification
- `bun test components/campaigns/detail/__tests__/CampaignKpiBar.test.tsx` (runner passed; jsdom test skipped under Bun's non-DOM environment)
- `git diff --check`
- Blocked locally: `bunx vitest run components/campaigns/detail/__tests__/CampaignKpiBar.test.tsx` due to Rollup native optional dependency code-signature issue
- Blocked locally: `bun run typecheck` due to unrelated existing Prisma/user typing errors
