# Issue 1430 - Customer Management pagination and last login

## Intake
- Source: GitHub issue 1430 in `primest-spec/flowfox`.
- Scope: Customer Management table/card rendering and `/api/customer-management/customers` enrichment.
- Project profile: Flowfox.
- Risk: Medium.

## Requirements Summary
- Show 20 customer rows/cards per page after search filtering.
- Add pagination controls below Customer Management results.
- Reset pagination to page 1 when search changes.
- Keep summary cards based on the full customer list.
- Add last-login display with German date/time or dash for never-login.
- Show `Inaktiv (>1 Monat)` for dormant users and `Noch nie eingeloggt` for never-login users.
- Reuse shared login status logic and align badge styling with User Management.

## Plan
- Reuse `deriveLoginStatus` from `lib/users/login-status.ts`.
- Extract User Management login display formatting/badge to a shared UI helper.
- Enrich Customer Management API rows with Supabase Auth `last_sign_in_at` and `login_status`.
- Paginate the filtered array in `CustomerManagement.tsx`.
- Add focused tests/source checks plus TypeScript and diff checks.
- Capture local frontend evidence if environment permits.

## Progress
- 2026-06-23: Intake, policy/profile review, and implementation planning started.
- 2026-06-23: Implemented shared login display, Customer Management API enrichment, client-side pagination, and focused tests.
- 2026-06-23: Verification passed for focused Vitest, TypeScript, and diff whitespace checks.
- 2026-06-23: Local browser evidence was blocked by missing authenticated customer portal session; route redirected to login. Draft publication required until authenticated table verification is available.
- 2026-06-23: Correction after user feedback: closed draft PR #1476, deleted `issue/1430-customer-management-pagination-login`, committed the same work to current branch `feat/customer-management-paginate-customer-table`, and pushed commit `0b056d06cef5d9d1ba61c53ceb575b180d3f2357`.

## Verification
- `bun node_modules/vitest/vitest.mjs run lib/users/login-status.test.ts lib/users/login-display.test.ts components/customer-management/__tests__/customer-management-layout.test.ts` - pass, 14 tests.
- `bun node_modules/typescript/lib/tsc.js --noEmit` - pass.
- `git diff HEAD --check` - pass.
- Frontend attempt: `/customer-portal/customer-management` redirected to login without session; blocker screenshot stored privately.
