- Date: 2026-04-16
  Agent: Codex
  Failure: Contact-specific trigger logic had spread into small free helper functions and a dedicated post-save hook.
  Root cause: The implementation optimized for local convenience instead of the repository preference for behavior living on the owning model.
  Prevention rule: If logic mainly operates on one `ClickfunnelsContact`, implement it as a member method on `ClickfunnelsContact` unless there is a strong cross-model reason not to.
  Example bad pattern: `contact_is_sellable_for_outgoing_custom_conversion_trigger(contact)` plus a separate signal wrapper.
  Example good pattern: `contact.can_be_transferred_for_outgoing_custom_conversion_trigger()` called from the model lifecycle point that already owns the business transition.
  Scope: repository-wide

- Date: 2026-04-16
  Agent: Codex
  Failure: The same business event (`ContactCanBeTransferred`) was wired into multiple lifecycle points and could fire more than once for one contact.
  Root cause: The implementation followed technical state transitions like verify-phone success instead of choosing one canonical business event boundary.
  Prevention rule: For event-style tracking, choose one canonical lifecycle point and route all paths through it instead of firing from intermediate status updates plus final action attempts.
  Example bad pattern: firing once on phone-validation success and again on the first transfer attempt.
  Example good pattern: fire only from `TransferMethod.transfer_contact_impl()` when the first real transfer attempt begins.
  Scope: repository-wide

- Date: 2026-04-20
  Agent: Codex
  Failure: A clean modern runtime exposed multiple undeclared startup dependencies that old long-lived virtualenvs had been masking.
  Root cause: The repository relied on historical environment drift instead of declaring every direct runtime dependency needed during Django startup and app import.
  Prevention rule: When upgrading Python or Django, validate startup from a fresh isolated venv and add every direct startup/runtime dependency explicitly to the target requirements file instead of assuming the old environment contents.
  Example bad pattern: code imports `inflection`, `psutil`, `fasteners`, `facebook_business`, `openai`, `pygsheets`, or `django_redis` during startup without pinning them in the runtime requirements.
  Example good pattern: declare direct startup/runtime dependencies explicitly in the target requirements set and verify `manage.py check`, `manage.py shell`, and `runserver` from a clean venv.
  Scope: repository-wide

- Date: 2026-04-22
  Agent: Codex
  Failure: A management command started timers in `__init__()`, so even `manage.py <command> --help` could hang or need external termination.
  Root cause: Runtime guard setup was tied to command construction instead of actual command execution.
  Prevention rule: For Django management commands, keep `__init__()` side-effect free; start timers, threads, signal handlers, and long-lived guards only inside `handle()` or a method called from it.
  Example bad pattern: constructing `Timer(...)` objects and starting them inside `Command.__init__()`.
  Example good pattern: initialize plain attributes in `__init__()` and arm timers only when `handle()` begins.
  Scope: repository-wide

- Date: 2026-04-22
  Agent: Codex
  Failure: Upgrading a Django app package on py311 without aligning the legacy table schema broke admin views because the model started selecting columns that do not exist in the database yet.
  Root cause: Package/model upgrades outpaced schema alignment on a legacy database, and admin code assumed the full modern model field set always exists physically.
  Prevention rule: When validating py311 against legacy databases, compare model fields with real table columns for third-party apps before assuming admin/model surfaces are safe; prefer narrow compatibility guards over autonomous schema mutation when the task is just to restore the test stand.
  Example bad pattern: upgrading `django_celery_results` and letting admin query `periodic_task_name` or `date_started` against an older TaskResult table.
  Example good pattern: detect missing DB-backed fields dynamically in admin/query surfaces, restore functionality, and schedule schema alignment as a separate deliberate task.
  Scope: repository-wide

- Date: 2026-04-23
  Agent: Codex
  Failure: A broad admin sweep initially produced unusable results because noisy startup prints from Django app `ready()` methods polluted machine-readable stdout.
  Root cause: The sweep harness assumed exclusive control of stdout even though the application imports emit legacy debug prints during startup.
  Prevention rule: When building automation against this repository, expect import-time stdout noise; either parse the last JSON object from output or isolate structured output onto a dedicated stream or file.
  Example bad pattern: calling `json.loads(completed.stdout)` directly and assuming it contains only JSON.
  Example good pattern: extract the final JSON line or write structured output to a separate file while tolerating startup logs.
  Scope: repository-wide

- Date: 2026-04-23
  Agent: Codex
  Failure: A single pathological admin changelist can hang a whole sweep if all models are checked in one long-lived process.
  Root cause: Several legacy admin surfaces perform enough work during GET rendering that per-request timeouts are not sufficient to protect the whole run.
  Prevention rule: For large admin sweeps on this repository, isolate each model probe in its own subprocess with a hard wall-clock timeout so one legacy page cannot block the full validation pass.
  Example bad pattern: iterating every admin model inside one Django process and relying on soft request-level timeouts only.
  Example good pattern: spawn one child process per model, enforce a hard timeout, log progress incrementally, and keep the overall run moving.
  Scope: repository-wide

- Date: 2026-04-24
  Agent: Codex
  Failure: The first server-side URL sweep misclassified every safe probe as a failure because the py311 runtime on the test host could not verify the `contactapi2.static.fyi` certificate chain.
  Root cause: The harness treated TLS trust-store failure as if it were an application route failure.
  Prevention rule: For internal contactapi2 test-stand HTTP sweeps, separate transport/TLS failures from backend failures; use a controlled unverified HTTPS context or a known-good local nginx path before classifying route health.
  Example bad pattern: reporting `CERTIFICATE_VERIFY_FAILED` as 66 backend failures.
  Example good pattern: rerun with explicit internal-test TLS handling, then check HTTP status and uWSGI logs for actual 500/traceback evidence.
  Scope: py311 test-stand sweeps

- Date: 2026-04-27
  Agent: Codex
  Failure: A focused clickfunnels probe initially hung because it used `SavedHttpRequest.path/full_path__icontains` on a large legacy table.
  Root cause: The harness used convenient ORM substring filtering instead of walking recent primary-key ranges and filtering route strings in Python.
  Prevention rule: For large legacy request-log tables, avoid broad `icontains` discovery during py311 sweeps; scan bounded PK batches and only hydrate full rows after selecting candidate IDs.
  Example bad pattern: `SavedHttpRequest.objects.filter(path__icontains="webflow-ajax")[:5000]`.
  Example good pattern: fetch recent `id/path/full_path` rows ordered by indexed primary key, filter in Python, then load only the few matching rows needed for replay.
  Scope: py311 test-stand sweeps

- Date: 2026-05-21
  Agent: Codex
  Failure: `bun run quality-check:fast` auto-ran `prettier . --write` and then attempted global ESLint fixes after baseline formatting failures, creating broad unrelated churn during a narrow Flowfox task.
  Root cause: The project quality wrapper is not check-only when baseline checks fail; it tries to repair the whole workspace.
  Prevention rule: For narrow Flowfox tasks, avoid `bun run quality-check:fast` unless broad auto-format/auto-fix churn is acceptable; use focused Vitest, targeted ESLint, `bun run typecheck`, `git diff --check`, and agent workspace checks instead.
  Example bad pattern: running `bun run quality-check:fast` after a small Brand Set patch and then having to clean formatter fallout.
  Example good pattern: run `bun node_modules/vitest/vitest.mjs <files>`, targeted `./node_modules/.bin/eslint <changed files>`, `bun run typecheck`, and record any baseline-wide quality wrapper limitation.
  Scope: Flowfox local verification

- Date: 2026-05-21
  Agent: Codex
  Failure: `bun run typecheck` invoked an old system Node that could not parse the installed TypeScript entrypoint, producing `SyntaxError: Unexpected token {`.
  Root cause: The script resolves `tsc` through the local shell/Node path instead of forcing Bun's runtime.
  Prevention rule: For Flowfox type checks on this workstation, run `bun node_modules/typescript/lib/tsc.js --noEmit` when the package script hits the old Node path.
  Example bad pattern: assuming `bun run typecheck` always executes TypeScript with a modern Node-compatible runtime.
  Example good pattern: use the direct Bun TypeScript invocation and record any sandbox write escalation needed for `tsconfig.tsbuildinfo`.
  Scope: Flowfox local verification

- Date: 2026-05-21
  Agent: Codex
  Failure: A Sanity Studio-specific typecheck initially could not resolve root `@/...` imports from Studio-imported components.
  Root cause: `apps/studio/tsconfig.json` overrode `baseUrl` without remapping the inherited root path alias, while root `tsconfig.json` excludes `apps/studio`.
  Prevention rule: For Flowfox Studio changes, run `bun node_modules/typescript/lib/tsc.js --noEmit -p apps/studio/tsconfig.json --incremental false` and keep `@/*` mapped back to `../../*`.
  Example bad pattern: relying only on the root typecheck for changes under `apps/studio`.
  Example good pattern: run both root and Studio typechecks when editing Sanity config, schemas, or Studio components.
  Scope: Flowfox Sanity Studio verification

- Date: 2026-05-22
  Agent: Codex
  Failure: Advertorial section labels were hard to identify in Sanity when sections shared similar structure or empty headings.
  Root cause: Section array previews relied on visible content fields only and did not have editor-only naming metadata.
  Prevention rule: For Advertorial CMS section naming, use the optional `sectionName` field as editor-only metadata and keep preview fallback order as `sectionName` > `heading` / `headline` > section type label.
  Example bad pattern: adding type-specific public labels or rendering custom section names on the advertorial page without an explicit product requirement.
  Example good pattern: add `sectionName` to Sanity section objects and use it only in Studio previews.
  Scope: Flowfox Sanity Studio schemas

- Date: 2026-05-22
  Agent: Codex
  Failure: Local Sanity Studio crashed because a browser component imported `@/app/api/cms/actions`, which imports Prisma through `lib/db`.
  Root cause: Server actions were reused directly in Studio browser code instead of being called through an HTTP boundary.
  Prevention rule: Studio browser components must use browser-safe API bridge helpers for CMS server actions; never import Prisma-backed server actions into the Vite bundle.
  Example bad pattern: `import { regenerateText } from "@/app/api/cms/actions"` inside a Studio input component.
  Example good pattern: `fetch("/api/cms/studio/regenerate-text")` through a typed Studio helper and a validated Next route that delegates to the server action.
  Scope: Flowfox Sanity Studio browser components

- Date: 2026-05-22
  Agent: Codex
  Failure: Sanity Presentation preview could render the Flowfox dashboard shell inside the iframe, creating nested sidebars.
  Root cause: `presentationTool.previewUrl.initial` pointed at the app root instead of a public CMS route, and document locations were not configured for Advertorial preview paths.
  Prevention rule: For Flowfox Sanity Presentation changes, keep the initial iframe URL on a no-dashboard public CMS fallback and resolve documents to public CMS routes.
  Example bad pattern: `previewUrl: { initial: previewOrigin }` when `previewOrigin` is the Flowfox app root.
  Example good pattern: set `initial` to a public CMS fallback and configure `resolve.mainDocuments` plus `resolve.locations` for `/a/<slug>` and `/l/<slug>`.
  Scope: Flowfox Sanity Presentation previews

- Date: 2026-05-26
  Agent: Codex
  Failure: Dialfire manual calls can be hidden if the Agent Limits overview treats `preview` as a separate tab only.
  Root cause: The supervisor overview needs predictive and manual counters side by side, while limit evaluation still follows the selected mode.
  Prevention rule: For Flowfox Dialfire Agent Limits, read manual calls from `connections.technology='preview'`, expose `predictiveCalls`, `manualCalls`, and `totalCalls`, and keep derived `callKind='total'` alert-only.
  Example bad pattern: adding a new `manual_calls` migration before confirming the existing Dialfire sync DB already stores manual calls as `technology='preview'`.
  Example good pattern: derive total calls from predictive + preview in `lib/dialfire/agent-calls.ts` and verify `overview`, `config`, and `enforce` route tests together.
  Scope: Flowfox Dialfire Agent Limits

- Date: 2026-05-26
  Agent: Codex
  Failure: A focused Dialfire config route test could fail before collecting tests because named `z` from `zod` evaluated as undefined under Bun/Vitest interop.
  Root cause: Module interop differed between direct Bun import and the Vitest route test transform.
  Prevention rule: Use a default `zod` import in that route if the named import fails under focused Vitest; keep validation behavior unchanged.
  Example bad pattern: assuming direct `bun -e` import behavior always matches Vitest route transforms.
  Example good pattern: `import z from "zod"` in `app/api/dialfire-agent-limits/config/route.ts`.
  Scope: Flowfox route tests

- Date: 2026-05-26
  Agent: Codex
  Failure: Landing Page rich typography could break existing campaigns if `headline` / `subhead` string fields are replaced instead of extended.
  Root cause: Sanity documents already store published hero copy as strings, and changing those field types would require a migration and can hide content in Studio/public renderers.
  Prevention rule: For Flowfox Landing Page typography, keep legacy string fallbacks and add optional `headlineRich` / `subheadRich` Portable Text fields plus `overline` and `headlineSize`; render rich marks inline inside H1/H2 without raw HTML.
  Example bad pattern: changing `headline` from `string` to Portable Text and relying on all documents to migrate immediately.
  Example good pattern: query both rich and plain fields, render rich fields when present, and fall back to plain strings for old campaigns.
  Scope: Flowfox Landing Page CMS rendering

- Date: 2026-05-27
  Agent: Codex
  Failure: Desktop and mobile dashboard navigation can drift when each renderer owns a separate item array.
  Root cause: Permission and URL metadata were duplicated in `app/(dashboard)/sidebar.tsx` and `components/dashboard/MobileNavigation.tsx`; `Usage Tracking` used different permissions across breakpoints.
  Prevention rule: For Flowfox dashboard navigation, keep URL, icon, permission, role group, feature flag, and nesting metadata centralized in `lib/dashboard-navigation.ts`.
  Example bad pattern: adding a menu item to desktop and separately recreating it in mobile with a different permission key.
  Example good pattern: update `DASHBOARD_NAVIGATION`, then verify with `lib/__tests__/dashboard-navigation.test.ts`.
  Scope: Flowfox dashboard navigation

- Date: 2026-05-29
  Agent: Codex
  Failure: Customer Portal sidebar labels can remain hardcoded in English while the surrounding CheckfoxPro UI is German.
  Root cause: The active sidebar owned visible labels directly inside JSX instead of using a small localized navigation helper.
  Prevention rule: For Flowfox Customer Portal sidebar label changes, keep labels and route matching centralized in `lib/customer-portal/sidebar-navigation.ts`, source localized labels from `dictionaries/de.json`, and keep active-state matching path-based.
  Example bad pattern: `{ label: "Projects", href: "/customer-portal/projects" }` inside `app/customer-portal/sidebar.tsx`.
  Example good pattern: `CUSTOMER_PORTAL_PRIMARY_SIDEBAR_ITEMS` with `label: deDictionary.sidebar.projects` and active checks against `path`.
  Scope: Flowfox Customer Portal navigation

- Date: 2026-05-29
  Agent: Codex
  Failure: Advanced CTA vertical spacing can drift from Content Section if new parallel padding fields are introduced.
  Root cause: Issue language may mention padding, but the Flowfox Advertorial schema already models section rhythm with `marginTop` and `marginBottom`.
  Prevention rule: For Flowfox Advertorial Advanced CTA spacing, reuse the Content Section `marginTop` / `marginBottom` fields and shared responsive class mapping unless the product intentionally renames the section spacing model.
  Example bad pattern: adding `paddingTop` and `paddingBottom` only to `advancedCta`.
  Example good pattern: spreading the same `verticalSpacingFields` into `contentSection` and `advancedCta`, then rendering both through shared spacing class logic.
  Scope: Flowfox Sanity Advertorial sections

- Date: 2026-05-30
  Agent: Codex
  Failure: Campaign offer types can drift when UI, quick-create mapping, Prisma enum values, and AI prompts each own separate literal lists.
  Root cause: Offer type metadata was duplicated across campaign setup surfaces and prompt builders.
  Prevention rule: For Flowfox Campaign Offer Type changes, centralize values, labels, aliases, and prompt guidance in `lib/campaign-offer-types.ts`; update focused tests and record required database enum rollout separately from code changes.
  Example bad pattern: adding an e-commerce option only to one dropdown or mapping product campaigns back to eBook/Whitepaper.
  Example good pattern: add `ECOMMERCE_PRODUCT` to the shared helper, route it through create/edit/quick-create, and give AI product-sale CTA guidance.
  Scope: Flowfox campaign setup and AI generation

- Date: 2026-05-30
  Agent: Codex
  Failure: The Tools & Utilities Account Management card used a generic/unsupported icon token and rendered like a placeholder instead of a domain-specific account icon.
  Root cause: The hub card icon list accepted arbitrary FontAwesome names without a local visual/render check.
  Prevention rule: For Flowfox Tools & Utilities hub cards, use concrete FontAwesome icon names already known to render in the project style and visually verify the hub when changing icon tokens.
  Example bad pattern: assigning an unsupported metaphor icon such as `kanban` and relying on browser fallback behavior.
  Example good pattern: use `user-gear` for Account Management so the purple card keeps a specific account-management glyph.
  Scope: Flowfox Tools & Utilities hub UI

- Date: 2026-06-01
  Agent: Codex
  Failure: Advertorial translation duplication can accidentally mutate source documents, duplicate media metadata, or create competing localized slugs if it is bolted onto generic duplicate logic without a tested mapping layer.
  Root cause: AI translation spans nested Sanity copy fields, raw HTML strings, media references, slug metadata, and source tracking at the same time.
  Prevention rule: For Flowfox Advertorial Translate & Duplicate, implement a separate Sanity clone flow, preserve media/reference/system fields, translate collected copy fields through a focused helper, fail clearly on existing localized slugs, and keep metadata additive in Sanity unless a Prisma migration is explicitly requested.
  Example bad pattern: patching the original Advertorial or blindly sending the full Sanity document through the LLM.
  Example good pattern: collect translatable string paths, apply returned strings to a cloned document, set `duplicationSourceId` / translation metadata, and create `<source-slug>-<locale>` only when it is free.
  Scope: Flowfox Advertorials and Sanity CMS

- Date: 2026-06-01
  Agent: Codex
  Failure: Pre-image attribution can be lost or styled inconsistently if it is modeled as an ad hoc image-only property and only wired into one render path.
  Root cause: Advertorial Content Section image data flows through Sanity schema, public GROQ, shared inline image rendering, and `workspace-keep` draft publishing.
  Prevention rule: For Flowfox Advertorial Content Section pre-image attribution, use optional section-level `preImageAttribution`, render it above the inline image with the existing `advertorial-image-caption` class, and keep public GROQ plus `workspace-keep` preservation in sync.
  Example bad pattern: adding a visible field in Studio but not querying it on `/a/<slug>`, or rendering it with a new caption style that drifts from Image Caption.
  Example good pattern: add the section field before `image`, fetch it in the contentSection projection, pass it to `AdvertorialInlineImage`, and cover both render/no-empty-node plus publish preservation.
  Scope: Flowfox Advertorial Content Sections

- Date: 2026-06-02
  Agent: Codex
  Failure: Landing Page Studio footer actions can drift from Advertorial actions if each document type owns separate view URL and publish footer logic.
  Root cause: The same Sanity Studio footer behavior needs document-specific route prefixes but shared preview/live mode selection, local Studio preview origin handling, fixed button layout, and default Publish hiding.
  Prevention rule: For Flowfox Sanity Studio content footer actions, keep Advertorial and Landing Page `View Live` / `View Preview` behavior on one shared helper and one footer component; route Advertorials to `/a/<slug>` and Landing Pages to `/l/<slug>`.
  Example bad pattern: adding a Landing Page-only button that hardcodes live URLs and bypasses `/api/preview` for drafts or autosaved documents.
  Example good pattern: use a shared `buildContentViewTarget` helper plus a content-type guard in the footer action, while preserving the existing Advertorial exports for compatibility.
  Scope: Flowfox Sanity Studio content footer actions

- Date: 2026-06-24
  Agent: Codex
  Failure: A stale Flowfox working branch briefly made a one-file UI fix appear to delete freshly merged neighboring code after `publish_pr.py --dry-run` fetched a newer `origin/main`.
  Root cause: The implementation diff was checked against local `HEAD` before publication, but the publisher builds its worktree from current `origin/main`.
  Prevention rule: For Flowfox publication, after the publication dry-run fetches or updates `origin/main`, compare the task-scoped diff against `origin/main` and repair any accidental rollback before the real publish step.
  Example bad pattern: publishing a selected file copied from a stale local branch when that file has changed on `origin/main`.
  Example good pattern: run `git diff origin/main -- <changed-file>` after dry-run fetches, ensure only the intended task hunk remains, then publish.
  Scope: Flowfox publication workflow
