# Issue 1423: Apply Settings overview card design to Toolbox page

## Summary

- Task: copy the compact dark Settings card style to `/tools` only.
- Scope: `app/(dashboard)/tools/ToolsBoardClient.tsx`.
- Risk: LOW.

## Implementation Notes

- Reworked `ToolsBoardClient.tsx` to use Settings-like section labels and compact grouped cards on `/tools`.
- Brightened cards with stronger color-tinted backgrounds, borders, and icon contrast.
- Preserved full-card click behavior with an overlay link while keeping the pin/menu popover independently clickable.
- Added section grouping for the current Toolbox catalog.
- Added a fallback `Weitere Tools` section for future catalog entries that are not in the explicit section order.
- Kept pin/menu personalization behavior and `/settings/menu-preferences` access.
- Left `/settings` and `lib/toolbox-catalog.ts` unchanged.

## Verification

- PASS: `bun node_modules/typescript/lib/tsc.js --noEmit`
- PASS: `bun node_modules/eslint/bin/eslint.js app/(dashboard)/tools/ToolsBoardClient.tsx`
- PASS: `git diff HEAD --check`
- PASS: changed-diff secret/env marker scan
- PASS: catalog coverage check confirmed 26/26 catalog entries are covered by explicit sections or fallback.

## Frontend Evidence

- Local server: `http://localhost:3000` using bundled modern Node.
- Screenshot: `/Users/user/flowfox/artifacts/toolbox-brighter-cards-1423-local.png`
- Note: Playwright's isolated browser profile redirected to sign-in, so final evidence was captured from the user's authenticated Chrome profile.

## Publication

<!-- publication-result:start -->
## Publication Result

- Execution status: `completed`
- Branch: `feat/apply-settings-overview-card-design-to-toolbox-page`
- Base branch: `main`
- Commit SHA: `96ff57bf6d722670570c0ad2bf5b582d43649e1b`
- Branch pushed: `True`
- PR URL: `https://github.com/PrimestSpec/flowfox/pull/1469`
- PR state: `ready`
- PR comment posted: `False`
- Warnings: executor temporary worktree lacked node_modules for its profile quality command; publish_pr.py hit the merged-PR fallback case and PR #1469 was created with gh after the mandatory gate/push.
<!-- publication-result:end -->
