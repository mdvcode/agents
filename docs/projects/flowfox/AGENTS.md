# Flowfox Agent Rules

## Project Profile
- Treat Flowfox as a Next.js 15 / React 19 / Prisma / Sanity / Bun / TypeScript repository, not a Django repository.
- Preserve the existing app-router layout under `app/`, shared helpers under `lib/`, and CMS components under `components/cms/`.
- Public pages must keep no-auth access intentional and must not broaden authenticated dashboard APIs into public routes.
- Sanity schema changes should stay backward compatible with existing documents.

## Publication
- Flowfox automated publication follows `.agent-policy.yaml` and `.agent-project-profiles.yaml`.
- Evidence exists and checks pass: create or update a ready PR.
- Evidence is unavailable or checks fail: create or update a draft PR with warnings.
- Secrets, HIGH risk, destructive actions, invalid artifacts, protected paths, production access, auto-merge, deploy, force-push, or history rewrite block publication.
- Commit every task-scoped changed/added/deleted public project file exactly once in a minimal reviewable commit.
- Use the repository's configured `git config user.name` and `git config user.email`; do not hardcode or change identity.
- Never commit, push, or include private control-plane files or paths such as `/Users/user/agents`, `external/agents/`, `.agents/`, `artifacts/`, private issue journals, private memory/wiki/graph files, screenshots/traces with sensitive data, or agent prompt/skill/audit files.
- Branch names, commit messages, PR titles, PR bodies, issue comments, and release notes must follow `.agent-policy.yaml` `public_output_forbidden_phrases`. Product terms such as AI are allowed when they describe user-facing product behavior rather than the internal development process.
- After publication, send the PR URL plus the local website URL where the completed issue can be checked.

## Evidence
- Provide local-site evidence before publication whenever the change affects UI, routing, public CMS rendering, Studio UI, dashboard UI, or user-visible behavior.
- Start or reuse the local dev server, open the affected local URL, and attach at least one screenshot.
- Capture a short video or trace when motion, multi-step interaction, responsive behavior, modal flows, or before/after behavior matters.
- If local visual evidence is impossible, record the blocker or warning and publish only as a draft PR when policy allows.

## Verification
- For narrow Flowfox work, avoid `bun run quality-check:fast` unless broad auto-format/auto-fix churn is acceptable.
- Prefer focused Vitest, targeted ESLint, `bun node_modules/typescript/lib/tsc.js --noEmit`, and `git diff HEAD --check`.
- For Sanity Studio work, run `bun node_modules/typescript/lib/tsc.js --noEmit -p apps/studio/tsconfig.json --incremental false` in addition to the root typecheck.
- The Studio `@/*` path alias should resolve to the repository root.

## Durable Rules
- Keep SurveyJS rendering client-only; do not introduce server imports of SurveyJS UI packages.
- For Brand Set styling, prefer shared tokens from `lib/brand-set-css.ts` and the existing `BrandSetSsrStyleTag` / `BrandSetOverrideListener` path over one-off component colors.
- When a component has local style overrides, preserve fallback order: local component setting > Brand Set token > system default.
- For Advertorial CTA text colors, preserve the Sanity key `colors.textColor`; do not introduce a parallel `buttonTextColor` field unless the schema intentionally changes.
- When publishing from Advertorial Workspace, keep CTA `colors` in `workspace-keep`.
- For Brand Set disclaimer visibility, preserve `showDisclaimerAdvertorial` default `true` and `showDisclaimerLandingpage` default `false`; public forms should follow Landingpage behavior.
- For Advertorial CMS section naming, use optional Sanity key `sectionName` as CMS-only metadata and keep preview fallback order as `sectionName` > section headline (`heading` / `headline`) > section type label.
- For Advertorial Advanced CTA spacing, reuse Content Section Sanity keys `marginTop` / `marginBottom` and shared spacing class logic.
- For Advertorial Content Section pre-image attribution, use optional section-level `preImageAttribution`, render it above the inline image with `advertorial-image-caption`, and preserve it through public GROQ plus `workspace-keep`.
- For Sanity Studio content footer actions, keep Advertorial and Landing Page `View Live` / `View Preview` behavior on one shared URL/action helper: Advertorial routes use `/a/<slug>`, Landing Pages use `/l/<slug>`, drafts/autosaves use `/api/preview`.
- For Sanity Presentation previews, keep the iframe on public CMS routes. Do not use the app/dashboard root as `previewUrl.initial`; configure document locations for `/a/<slug>` and `/l/<slug>`.
- For Dialfire Agent Limits, manual calls are Dialfire `technology='preview'`; show them alongside predictive calls, and keep derived total-call limits alert-only.
- For Landing Page typography, keep legacy `headline` / `subhead` string fallbacks; add rich formatting through optional `headlineRich` / `subheadRich` Portable Text fields plus `overline` and `headlineSize`.
- For Advertorial Translate & Duplicate, reuse the Sanity clone/source-tracking pattern, preserve media/reference/system fields, keep localized slugs deterministic (`<source-slug>-<locale>`), and add optional Sanity metadata without Prisma migrations unless explicitly requested.
- For Campaign Offer Type changes, keep values centralized in `lib/campaign-offer-types.ts`; use `ECOMMERCE_PRODUCT` for physical product sales and e-commerce.
- For dashboard navigation, keep desktop and mobile navigation on `lib/dashboard-navigation.ts`; every routed item must declare `href`, `requiredPermission`, `roleGroup`, and icon metadata.
- For Customer Portal sidebar localization, keep labels and route matching in `lib/customer-portal/sidebar-navigation.ts`; use German dictionary entries and path-based active-state matching.
- For Tools & Utilities hub cards, use concrete FontAwesome icon names known to render in the project style.
- For UI element removal issues, search exact visible text plus neighboring copy, link text, icon/comment markers, and shared component names across `app/`, `components/`, and `lib/`, excluding generated artifacts.
- Do not edit `prisma/migrations/**` autonomously. Schema changes may be prepared in `prisma/schema.prisma`, but migration creation/application needs explicit human approval.
