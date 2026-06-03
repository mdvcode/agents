# Review

## Scope

- Flowfox issue 897: Landing Page Sanity Studio `View Live` / preview footer action next to `Publish`.

## Findings

- Shared Studio URL helper now supports both Advertorial and Landing Page content routes.
- The existing Advertorial footer layout is reused for Landing Pages with fixed-width buttons and Publish pairing.
- Draft/autosaved documents use `/api/preview`; published documents without drafts use live public routes.
- Missing slugs disable the view action.
- No protected paths, migrations, dependencies, auth/session rules, billing, payments, secrets, webhooks, or production infrastructure are touched.

## Residual Risk

- Manual Studio visual verification on desktop and 13-inch viewport remains recommended.
- Dependency audit baseline remains unresolved outside this patch.
