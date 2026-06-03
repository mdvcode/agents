# Goal

## GOAL

- Implement `PrimestSpec/flowfox#897`: add a Landing Page Sanity Studio `View Live` / preview footer action beside `Publish`.

## CONTEXT

- Existing Advertorial footer action lives in `apps/studio/components/AdvertorialViewAction.tsx`.
- Existing Studio view URL helper lives in `apps/studio/lib/advertorial-view-url.ts`.
- Studio footer wiring lives in `apps/studio/sanity.config.tsx`.
- Focused URL tests live in `lib/__tests__/advertorial-view-url.test.ts`.

## CONSTRAINTS

- Match the existing Advertorial footer action dimensions, spacing, icon behavior, typography, and publish pairing.
- Use existing Landing Page `slug.current`; do not add schema fields or Prisma migrations.
- Disable the action when the active document has no valid slug.
- Use `/l/<slug>` for Landing Pages and `/a/<slug>` for Advertorials.
- Preserve draft/preview behavior for unpublished or autosaved documents.
- Avoid auth, permissions, billing, payment, secrets, dependency, migration, webhook, and production infrastructure changes.

## RISK

- LOW: Sanity Studio UI and deterministic URL helper changes only.

## PLAN

1. Extend the Studio URL helper for both Advertorial and Landing Page documents while preserving existing Advertorial exports.
2. Update the footer action component to render the same dual-action layout for `advertorial` and `landingPage`.
3. Wire the generalized footer into Sanity config.
4. Add focused tests for Landing Page live URLs, draft preview URLs, and missing-slug disabled state.
5. Update root/external agent guidance, issue journals, and required artifacts.
6. Run focused Vitest, targeted ESLint, root and Studio Bun TypeScript checks, `git diff --check`, detect-secrets, dependency audit, and external agent checks.

## DONE WHEN

- Landing Page documents show `View Live` directly left of `Publish` in the Sanity editor footer when published and slugged.
- Draft or locally edited Landing Pages open a preview URL in a new tab.
- Missing/empty Landing Page slugs disable the view action.
- Existing Advertorial footer action behavior is unchanged.
- Required artifacts, issue journals, verification, and audit log are updated.

## VERIFY

- `bun node_modules/vitest/vitest.mjs lib/__tests__/advertorial-view-url.test.ts`
- `bun node_modules/eslint/bin/eslint.js apps/studio/components/AdvertorialViewAction.tsx apps/studio/lib/advertorial-view-url.ts apps/studio/sanity.config.tsx lib/__tests__/advertorial-view-url.test.ts`
- `bun node_modules/typescript/lib/tsc.js --noEmit --incremental false`
- `bun node_modules/typescript/lib/tsc.js --noEmit -p apps/studio/tsconfig.json --incremental false`
- `git diff --check`
- `detect-secrets scan <changed safe files>`
- `bun audit`
- `make check` and `make security` in `external/agents`

## OUTPUT

- Landing Page Sanity Studio footer view action matching Advertorial footer action.
- Focused URL helper regression coverage.
- Updated agent guidance, issue journals, and required artifacts.

## STOP RULES

- Stop if implementation requires migrations, protected paths, auth/session/permission changes, billing/payment changes, secrets, dependency changes, webhook changes, or production deployment changes.
