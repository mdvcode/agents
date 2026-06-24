# Issue 1138 - Reduce Walkthrough Video preview size on landing page detail page

## 2026-06-23

### Intake

- GitHub issue: `https://github.com/PrimestSpec/flowfox/issues/1138`
- Label: Low
- Project profile: Flowfox
- Scope: dashboard landing page detail UI only

### Context

The oversized layout came from the Walkthrough-Video preview living inside a two-column-wide `PageGrid` area while the media element used full-width 16:9 sizing.

### Plan

- Keep shared `PageGrid` unchanged.
- Keep Customer Portal preview unchanged.
- Constrain only the dashboard Walkthrough-Video media and empty state in `app/(dashboard)/campaigns/[id]/landing-pages/[docId]/content.tsx`.

### Implementation

- Added `max-w-lg` to the video wrapper.
- Added `max-w-lg` to the empty state and reduced its vertical padding.
- Cleaned up existing targeted lint issues in the touched file by removing an unused import, removing an unused catch binding, and adding a captions track placeholder to the video element.

### Verification

- PASS: `bun node_modules/typescript/lib/tsc.js --noEmit`
- PASS: `git diff HEAD --check`
- PASS: `bun node_modules/eslint/bin/eslint.js 'app/(dashboard)/campaigns/[id]/landing-pages/[docId]/content.tsx'`
- WARN: `./node_modules/.bin/eslint ...` failed because `/usr/local/bin/node` is v8.9.4; Bun-powered ESLint passed.
- FRONTEND: local dev server started with bundled modern Node, but unauthenticated dashboard access redirected to `/authentication/sign-in`; sign-in compilation then hit a local Turbopack runtime error. Screenshot saved at `/Users/user/agents/artifacts/flowfox-issue-1138-auth-turbopack-blocker.png`.

### Risk

LOW. No high-risk or protected files changed.

### Publication Notes

Policy allows autonomous publication for LOW Flowfox tasks. Because visual evidence of the actual card was blocked, publication was initially created as a draft. User then requested removing the new PR/branch and moving the change to the current branch.

- Closed PR: `https://github.com/PrimestSpec/flowfox/pull/1480`
- Deleted branch: `issue/1138`
- Current branch: `fix/reduce-walkthrough-video-preview-size-on-landing-page-detail-page`
- Current commit: `d3a6aab7bbe7e53e13547b491956d23db107fd45`
