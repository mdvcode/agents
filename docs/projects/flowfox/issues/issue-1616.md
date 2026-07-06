# Issue 1616 - Landing Page Workspace campaign page browser

## Intake
- GitHub: https://github.com/PrimestSpec/flowfox/issues/1616
- Risk: medium
- Profile: flowfox
- Branch: `feat/landing-page-workspace-38-browse-campaign-landing-pages-and-open-existing-pages`

## Requirements
- List campaign landing pages in the Landing Page Workspace via `/api/cms/landingPagesByCampaign`.
- Show title, status, readable template type, last updated, and unsaved draft state.
- Open an existing page in the workspace by resolving or creating `landing_page_drafts`.
- Support template type filter: Standard / Advanced / Bestellseite.
- Support deep link `?campaignId=x&docId=y`.
- Preserve the detail page launcher behavior.

## Notes
- #1614 and #1615 are still open on GitHub at intake.
- The current branch initially matched `origin/main`; implementation may need to add the minimal route shell expected by this issue.
- `external/agents` was dirty before task work and is not part of this issue.

## Implementation summary
- Added a standalone Landing Page Workspace dashboard route with campaign selection and Campaign Pages list.
- Added template filtering and readable labels for Standard, Advanced, and Bestellseite.
- Extracted detail launcher draft resolution into `lib/landing-page-workspace/resolve-draft-client.ts`.
- Added a campaign page read endpoint for opening/migrating existing Sanity landing pages.
- Updated dashboard navigation and focused tests.

## Verification
- Focused Vitest: 39 passed.
- TypeScript no-emit: passed.
- Targeted ESLint through Bun runtime: passed.
- `git diff HEAD --check`: passed.
- Changed-file secret/env scan: passed.
- Frontend evidence is partial: local route compiles but redirects to sign-in without a dashboard session.

## Publication
- Branch: `feat/landing-page-workspace-38-browse-campaign-landing-pages-and-open-existing-pages`
- Commit: `245eaf0b94ce6ea30c444a314b0f1b21b6344be2`
- PR: https://github.com/PrimestSpec/flowfox/pull/1674
- State: draft
