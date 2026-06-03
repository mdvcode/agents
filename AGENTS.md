# AGENTS.md

## Mission
Build safe, reviewable improvements to this Django repository with minimal diffs, explicit verification, and repository-local artifacts that make autonomous work auditable.

## Agent workspace model
- Treat this repository as the local home base for agents: prompts, skills, docs, logs, kanban boards, and audit artifacts live here.
- New agents should start with `docs/onboarding.md`, then read `AGENTS.md`, `artifacts/lessons_learned.md`, and the current `artifacts/plan.md`.
- Treat `/Users/user/agents` as a private control plane. Do not assume its memory files can be committed to any target project repository.
- Use git as the history of agent work. Keep changes small, reviewable, and traceable through `artifacts/audit_log.jsonl`.
- Store durable process documentation in `docs/`, not in one-off task artifacts.
- For multiple projects, keep private project memory under `docs/projects/<project>/`.
- Use markdown kanban boards under `docs/kanban/`:
  - `docs/kanban/tasks.md` for active execution work and process state.
  - `docs/kanban/tests-and-fixes.md` for failing checks, fixes, and retests.
  - `docs/kanban/features.md` for feature ideas and delivery slices.
- Keep per-issue execution history in `docs/projects/<project>/issues/issue-<number>.md`; every GitHub issue branch should have one matching issue journal.
- Maintain global agent knowledge in `docs/wiki/`, `docs/memory/`, and `docs/graph/`; maintain project-specific private knowledge in `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, and `docs/projects/<project>/graph/`.
- Use `docs/templates/goal.md` or the same structure in `artifacts/plan.md` before non-trivial implementation.
- Validate structured artifacts with `make validate-artifacts` or `make check`.
- The expected output of autonomous work is a local git repository state with code, docs, logs, artifacts, and a clear verdict.

## Privacy and publication rules
- Private execution memory stays in `/Users/user/agents` by default.
- Target project repositories should receive only reviewed code, tests, migrations when explicitly approved, and safe public documentation.
- Do not publish `docs/projects/*/issues/`, `docs/projects/*/memory/`, `docs/projects/*/wiki/`, `docs/projects/*/graph/`, or `artifacts/` into a target project repository unless the user explicitly approves.
- Before copying any memory into a PR, issue comment, commit message, or project documentation, sanitize it: remove secrets, tokens, private customer data, internal reasoning traces, private URLs, and unnecessary names.
- Project privacy policy lives in `docs/projects/<project>/privacy.md` and must be read before work on that project.
- A GitHub issue is not worked automatically when it appears. Start issue work only when the user explicitly gives a project and issue number, unless the user has created a separate automation for monitoring.

## Global operating rules
- Prefer minimal, reversible changes.
- Never claim success without running verification tools.
- Inspect existing code patterns before changing structure.
- Update tests and docs when behavior changes.
- Avoid broad rewrites unless absolutely necessary.
- Keep diffs reviewable.

## Flowfox rules
- Treat Flowfox as a Next.js 15 / React 19 / Prisma / Sanity repository, not a Django repository.
- Preserve the existing app-router layout under `app/`, shared helpers under `lib/`, and CMS components under `components/cms/`.
- For Flowfox issue completion, provide visual local-site evidence before requesting approval whenever the change affects UI, routing, public CMS rendering, Studio UI, dashboard UI, or user-visible behavior. Start or reuse the local dev server, open the affected local URL, and attach at least one screenshot; capture a short video/trace when motion, multi-step interaction, responsive behavior, modal flows, or before/after behavior matters. If local visual evidence is impossible, record the blocker and do not claim approve-ready completion.
- Flowfox approve gate: after implementation and verification, stop at `await_approval` with the evidence links, changed-file summary, checks, risk, and blockers. Do not commit, push, or open/update a PR until the user explicitly approves that exact completed state.
- After explicit Flowfox approval, commit only the reviewed changed/added/deleted files that belong to the approved scope. Use the repository's configured identity from `git config user.name` and `git config user.email`; do not hardcode another name/email and do not change git identity unless the user explicitly asks. If either value is missing, stop and ask the user to configure it.
- Never commit, push, or include in a Flowfox PR any private control-plane files or paths such as `/Users/user/agents`, `external/agents/`, `.agents/`, `artifacts/`, private `docs/projects/*/issues/`, private `docs/projects/*/memory/`, private `docs/projects/*/wiki/`, private `docs/projects/*/graph/`, screenshots/traces with sensitive data, or agent prompt/skill/audit files. Before staging, inspect `git status --short` and stage only approved public Flowfox project files.
- After explicit Flowfox approval and a successful commit, push the current issue branch to the user's GitHub remote and create or update a PR from that branch. The PR must use the authenticated GitHub account already available to `git`/`gh`, include a sanitized product/engineering summary, checks, risk, and safe local visual evidence references, and exclude private issue notes, secrets, raw screenshots with sensitive data, internal reasoning, agent files, and any mention that agents, Codex, AI, or automation performed the work.
- Keep SurveyJS rendering client-only; do not introduce server imports of SurveyJS UI packages.
- For Brand Set styling, prefer shared tokens from `lib/brand-set-css.ts` and the existing `BrandSetSsrStyleTag` / `BrandSetOverrideListener` path over one-off component colors.
- When a component has local style overrides, preserve the fallback order: local component setting > Brand Set token > system default.
- For Advertorial CTA text colors, preserve the existing Sanity key `colors.textColor`; do not introduce a parallel `buttonTextColor` field unless the schema intentionally changes.
- When publishing from Advertorial Workspace, keep CTA `colors` in `workspace-keep` so local preview choices survive into the Sanity document and public output.
- For Brand Set disclaimer visibility, preserve `showDisclaimerAdvertorial` default `true` and `showDisclaimerLandingpage` default `false`; public forms should follow the Landingpage behavior.
- For Advertorial CMS section naming, use the optional Sanity key `sectionName` as CMS-only metadata and keep preview fallback order as `sectionName` > section headline (`heading` / `headline`) > section type label.
- For Flowfox Advertorial Advanced CTA vertical spacing, reuse the existing Content Section Sanity keys `marginTop` / `marginBottom` and shared spacing class logic; do not add parallel `paddingTop` / `paddingBottom` fields unless the platform deliberately renames spacing controls.
- For Flowfox Advertorial Content Section pre-image attribution, use optional section-level `preImageAttribution`, render it above the inline image with the same `advertorial-image-caption` style as the existing caption, and preserve it through public GROQ plus `workspace-keep`.
- For Flowfox Sanity Studio content footer actions, keep Advertorial and Landing Page `View Live` / `View Preview` behavior on one shared URL/action helper: Advertorial routes use `/a/<slug>`, Landing Pages use `/l/<slug>`, drafts/autosaves use `/api/preview`, and the custom footer should hide the default Publish button only for supported content document types.
- For Sanity Presentation previews, keep the iframe on public CMS routes. Do not use the app/dashboard root as `previewUrl.initial`; configure document locations so Advertorials resolve to `/a/<slug>` and Landingpages resolve to `/l/<slug>`.
- For Dialfire Agent Limits, manual calls are Dialfire `technology='preview'`; show them alongside predictive calls, and keep derived total-call limits (`predictive + preview`) alert-only because there is no safe total-only Dialfire team removal path.
- For Landing Page advanced typography, preserve legacy `headline` / `subhead` string fallbacks; add rich formatting through optional `headlineRich` / `subheadRich` Portable Text fields plus `overline` and `headlineSize`, and render marks inline inside H1/H2 without raw HTML.
- For Flowfox Advertorial Translate & Duplicate work, reuse the existing Sanity clone/source-tracking pattern, preserve media/reference/system fields, keep localized slugs deterministic (`<source-slug>-<locale>`) with a clear conflict error, and add optional Sanity metadata without Prisma migrations unless explicitly requested.
- For Flowfox Campaign Offer Type changes, keep values centralized in `lib/campaign-offer-types.ts`; use `ECOMMERCE_PRODUCT` for physical product sales and e-commerce, and update AI prompt guidance so product-sale campaigns use purchase CTAs instead of lead-magnet/eBook language.
- For Flowfox dashboard navigation, keep desktop and mobile navigation on `lib/dashboard-navigation.ts`; every routed item must declare `href`, `requiredPermission`, `roleGroup`, and icon metadata, and permission filtering must remove empty parent containers.
- For Flowfox Customer Portal sidebar localization, keep labels and route matching in `lib/customer-portal/sidebar-navigation.ts`; use German dictionary entries for localized labels and keep active-state matching based on route paths, not visible text.
- For Flowfox Tools & Utilities hub cards, use concrete FontAwesome icon names that are already known to render in the project style; avoid generic or unsupported icons that can degrade to placeholder/question-mark glyphs.
- Do not edit `prisma/migrations/**` autonomously. Schema changes may be prepared in `prisma/schema.prisma`, but migration creation/application needs explicit human approval. If a user explicitly requests migration creation, keep the SQL minimal, classify risk as HIGH, and do not apply it to staging/production without a separate deploy confirmation.
- Public pages must keep no-auth access intentional and must not broaden authenticated dashboard APIs into public routes.
- Sanity schema changes should stay backward compatible with existing documents.

## Required verification loop
For every non-trivial task:
1. inspect relevant files
2. create or update `artifacts/plan.md`
3. classify risk
4. implement the minimal patch
5. run quality checks
6. run security checks
7. run tests
8. repair failures
9. re-run checks
10. update artifacts
11. update the project issue journal and private project memory/wiki/graph when durable project knowledge changed
12. decide the next action

## Python rules
- Prefer explicit typing where practical.
- Avoid mutable default arguments.
- Avoid broad `except` blocks.
- Avoid hidden side effects.
- Avoid dead code and speculative abstraction.
- Keep functions focused and cohesive.

## Django rules
- Do not place new business logic in views, admin classes, serializers, or forms unless that pattern is already clearly established nearby.
- Prefer service or domain-style helpers for new business logic.
- When logic clearly belongs to one model's state or behavior, prefer a member method or property on that model over introducing a new module-level helper function.
- Keep database access consistent with repository patterns.
- Avoid N+1 queries with `select_related` and `prefetch_related` where appropriate.
- Respect the existing settings module structure and `manage.py` boot path.
- Do not change migrations autonomously.
- Do not modify management commands destructively without explicit safety notes.

## DRF/API rules
- Public endpoints must have clear validation.
- Serializer validation must be explicit.
- Authentication and permission changes are high risk.
- Preserve backward compatibility unless the task explicitly requires behavior change.
- Prefer typed service functions and narrow serializer responsibilities.

## Test requirements
- Every new public behavior should have at least one test unless clearly impossible.
- Prefer pytest execution that remains compatible with the repository's existing Django `TestCase` style.
- Reuse fixtures and factories where possible.
- Include regression tests for bug fixes.
- Target 80 percent coverage unless the repository already defines a different threshold.

## Security rules
- No hardcoded secrets.
- No `shell=True`.
- No `eval` or `exec`.
- Validate external input.
- Use the ORM or parameterized queries only.
- Do not touch auth, billing, secrets, or production infrastructure autonomously.
- `detect-secrets` and dependency audit must be part of the pipeline.

## Autonomy gates
- LOW risk: may patch locally. Commit, push, and PR creation or updates should remain manual unless explicitly requested.
- MEDIUM risk: may patch and prepare a PR update, but no autonomous commit, auto-merge, or deploy.
- HIGH risk: may analyze and prepare a patch only, and must await human approval.
- Flowfox approval exception: when the user explicitly replies with approve/approved/аппрув/одобряю for a completed Flowfox issue state that includes local visual evidence and passing or documented checks, agents may commit the approved file set using the repository's configured `user.name`/`user.email`, push the branch, and create or update the PR. This exception never allows auto-merge, deployment, protected-path changes, secret publication, or unapproved scope expansion.
- Flowfox publication text rule: commit messages, branch descriptions, PR titles, PR bodies, issue comments, and release notes must not mention agents, Codex, AI assistance, automation, private control-plane files, `/Users/user/agents`, `external/agents`, `.agents`, or `artifacts`. Write them as normal human-authored product/engineering changes.

## Denylist and protected paths
Treat changes touching any of the following as HIGH risk and protected:
- `**/.env`
- `**/.env.*`
- `**/*.pem`
- `**/*.key`
- `**/*secret*`
- `**/migrations/**`
- `**/infra/prod/**`
- `**/terraform/**`
- `**/k8s/prod/**`
- `**/auth/**`
- `**/billing/**`
- `**/payments/**`
- `**/credentials/**`
- `**/secrets/**`
- `**/settings_prod.py`
- `**/settings/production.py`
- deployment scripts that affect production directly

## Required artifacts
- `artifacts/plan.md`
- `artifacts/risk.json`
- `artifacts/review.md`
- `artifacts/quality.json`
- `artifacts/security.md`
- `artifacts/verdict.json`
- `artifacts/report.md`
- `artifacts/lessons_learned.md`
- `artifacts/audit_log.jsonl`

## Artifact hygiene
- Keep `artifacts/` small and current.
- Required artifacts should describe the current task only.
- Copy durable issue history and final summaries into `docs/projects/<project>/issues/issue-<number>.md`; do not rely on `artifacts/` as long-term issue memory.
- Move durable project knowledge into `docs/projects/<project>/wiki/`, `docs/projects/<project>/memory/`, `docs/projects/<project>/graph/`, or `artifacts/lessons_learned.md`.
- Move only cross-project agent-system knowledge into global `docs/wiki/`, `docs/memory/`, or `docs/graph/`.
- Do not leave old probe scripts, large JSON dumps, or stale sweep reports in `artifacts/` after their findings have been summarized.
- If a future task needs temporary investigation outputs, create them intentionally and remove or summarize them before completion.

## Lessons learned policy
- Agents must read `AGENTS.md` and `artifacts/lessons_learned.md` before major conclusions.
- Recurring mistakes must be written into `artifacts/lessons_learned.md`.
- Stable lessons may be summarized in Persistent repository rules.
- Completion must be rejected if a known past mistake reappears without explanation.

## Done criteria
A task is not done until:
- relevant checks passed or blockers are explicitly recorded
- risk is classified
- artifacts are updated
- tests are added or updated if needed
- for Flowfox UI or user-visible work, local screenshot/video/trace evidence is captured or a blocker is recorded
- the next action is clearly stated
- an audit log entry is written for autonomous actions

## Persistent repository rules
- For Flowfox, preserve the Next.js app-router layout and Prisma schema-first data model.
- Treat any migration, auth, permission, session, CSRF, production settings, secret-management, webhook, payment, billing, or irreversible side-effect change as elevated risk.
- Treat public CMS rendering changes as at least LOW risk and verify with focused rendering or utility tests.
- Treat Brand Set visual-token changes as LOW risk when they only affect CSS fallback chains and preserve local overrides.
- Keep local agent memory under `external/agents/`; do not copy private issue journals into public PR text unless explicitly approved and sanitized.
- For narrow Flowfox work, avoid `bun run quality-check:fast` unless broad auto-format/auto-fix churn is acceptable; prefer focused Vitest, targeted ESLint, `bun node_modules/typescript/lib/tsc.js --noEmit` when `bun run typecheck` hits an old system Node, and `git diff --check`.
- For Flowfox Sanity Studio work, run `bun node_modules/typescript/lib/tsc.js --noEmit -p apps/studio/tsconfig.json --incremental false` in addition to the root typecheck; the Studio `@/*` path alias should resolve to the repository root.
- For Flowfox Advertorial section naming work, preserve `sectionName` as editor-only metadata unless the task explicitly asks to render it publicly.
- For Flowfox Sanity Presentation work, keep `previewUrl.initial` on a no-dashboard CMS fallback and use `resolve.mainDocuments` plus `resolve.locations` for `/a/<slug>` and `/l/<slug>`.
- For Flowfox Dialfire Agent Limits work, verify the Dialfire read-layer and all three route surfaces (`overview`, `config`, `enforce`) with focused Vitest. The derived `total` mode may use the existing string `callKind`; do not create migrations unless explicitly approved.
- For Flowfox Landing Page typography work, keep optional rich fields additive and migration-free, verify public hero rendering plus workspace normalization, and preserve plain text fallback behavior for existing campaigns.
- For Flowfox Advertorial Translate & Duplicate work, run focused Vitest for the translation mapping helper and API route, targeted ESLint for the overview UI/API/helper/tests, plus root and Studio Bun TypeScript checks; broad Studio schema Prettier failures may be recorded as baseline only when unrelated to the patch.
- For Flowfox Campaign Offer Type work, run focused Vitest for `lib/campaign-offer-types.ts` and affected prompt builders, targeted ESLint for changed UI/API/prompt files, and root Bun TypeScript; if a Prisma enum value is added, do not create migrations autonomously and record the required Supabase/Postgres rollout as a human action.
- For Flowfox main navigation work, run focused Vitest for `lib/dashboard-navigation.ts`, targeted ESLint for the navigation config/renderers, and root Bun TypeScript; verify mobile and desktop consume the same permission mapping before handoff.
- For Flowfox Customer Portal sidebar label work, run focused Vitest for `lib/customer-portal/sidebar-navigation.ts`, targeted ESLint for the helper and sidebar renderer, and root Bun TypeScript; verify translated labels do not alter route-based active highlighting.
- For Flowfox Advertorial Advanced CTA spacing work, run focused Vitest for the public CTA renderer, targeted ESLint for the schema/query/component/helper files, plus root and Studio Bun TypeScript checks.
- For Flowfox Advertorial Content Section pre-image attribution work, run focused Vitest for inline-image rendering and `workspace-keep` preservation, targeted ESLint for schema/query/component/route/helper/test files, plus root and Studio Bun TypeScript checks.
- For Flowfox Sanity Studio Landing Page/Advertorial footer action work, run focused Vitest for `apps/studio/lib/advertorial-view-url.ts`, targeted ESLint for the Studio action/config/helper/test files, plus root and Studio Bun TypeScript checks.
- For Flowfox Tools & Utilities icon-only fixes, run targeted ESLint/typecheck and visually verify the hub when possible; prefer existing account/domain FontAwesome icons such as `user-gear` over unsupported card metaphors.
