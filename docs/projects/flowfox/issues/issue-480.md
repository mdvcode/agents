# Issue 480: Add Brand Set Column to Campaign Overview Table

## Request

- Implement GitHub issue `primest-spec/flowfox#480`.
- Use Flowfox repository rules plus the private agent workspace in `external/agents`.
- Improve agent files where the work reveals better operating rules.
- Provide a commit message and sanitized PR text.

## Problem

- The campaign overview table did not show which Brand Set is assigned to each campaign.
- Users had to open campaigns individually to audit Brand Set assignments.
- Required behavior:
  - Add `Brand Set` between `Status & Zeitplan` and `Leistung`.
  - Show the linked Brand Set name.
  - Show a warning state when no Brand Set is assigned.
  - Handle stale/deleted Brand Set references.
  - Avoid mobile/tablet layout pressure.

## Risk

- Classified as LOW.
- Reason:
  - Uses an existing `campaigns.brandSetId` and `brandSet` relation.
  - No schema, migration, auth, billing, secret, or production infrastructure changes.
  - Existing authenticated campaign scoping remains unchanged.

## Plan

1. Fetch `brandSetId` and `brandSet { id, name }` in the campaign list page query.
2. Add a pure helper for assigned/missing/unknown/truncated Brand Set display states.
3. Render the new table column between status and performance.
4. Hide the column below the large breakpoint.
5. Add German dictionary keys and TypeScript dictionary typing.
6. Add focused utility coverage.
7. Update artifacts, lessons, and this issue journal.

## Implementation Notes

- Added `lib/campaign-brand-set-display.ts`.
- Added focused tests in `lib/__tests__/campaign-brand-set-display.test.ts`.
- Updated `app/(dashboard)/campaigns/list/page.tsx` to select:
  - `brandSetId`
  - `brandSet { id, name }`
- Updated `app/(dashboard)/campaigns/list/content.tsx`:
  - New `Brand Set` header after status.
  - Assigned state links to `/brand-set/<id>`.
  - `Fehlt` warning for missing assignments.
  - `Unbekannt` neutral state for stale references.
  - `hidden lg:table-cell` responsive behavior.
- Updated `dictionaries/de.json` and `lib/i18n.ts`.
- Updated root and private agent guidance with the direct Bun TypeScript check workaround.

## Verification

- Passed:
  - `bun node_modules/vitest/vitest.mjs lib/__tests__/campaign-brand-set-display.test.ts`
  - Targeted ESLint on changed issue files
  - `bun node_modules/typescript/lib/tsc.js --noEmit`
  - `git diff --check`
- Partial/security baseline:
  - `detect-secrets scan <changed issue files>` reports existing dictionary labels (`password`, `api_key`, `api_key_placeholder`) only.
  - `bun audit` reports existing dependency advisories unrelated to this patch.
- Agent workspace:
  - `make check`
  - `make security`

## Commit Message

`feat(campaigns): show brand set in campaign overview`

## PR Text

Summary:
- Add a desktop `Brand Set` column to the campaign overview table between status/timeline and performance.
- Fetch and render existing campaign Brand Set assignments with linked, truncated tags.
- Add missing and stale-reference fallback states with focused regression coverage.
- Update Flowfox agent guidance for the local Bun TypeScript verification path.

Tests:
- `bun node_modules/vitest/vitest.mjs lib/__tests__/campaign-brand-set-display.test.ts`
- `bun node_modules/eslint/bin/eslint.js 'app/(dashboard)/campaigns/list/content.tsx' 'app/(dashboard)/campaigns/list/page.tsx' lib/campaign-brand-set-display.ts lib/__tests__/campaign-brand-set-display.test.ts lib/i18n.ts`
- `bun node_modules/typescript/lib/tsc.js --noEmit`
- `git diff --check`
- `detect-secrets scan <changed issue files>` (baseline dictionary-label findings only)

Notes:
- No schema or migration changes.
- `bun audit` reports existing dependency advisories unrelated to this patch.
- The Brand Set column is hidden below `lg` to avoid mobile horizontal pressure.

## Next Action

- Review and commit the patch; address baseline dependency advisories separately.
