# Issue 1458: Add inactive filter to User Management tabs

## Summary

Add a client-side `Inaktiv (>1 Monat)` filter to `/settings/users` for Mitarbeiter, Kunden, and Affiliates. The filter should show only users with `login_status === "dormant"` and combine with the existing tab-scoped search and pagination.

## Context

- GitHub issue: https://github.com/PrimestSpec/flowfox/issues/1458
- Primary file: `app/(dashboard)/settings/users-card.tsx`
- Related helpers: `lib/users/login-display.ts`, `lib/users/login-status.ts`
- Current local branch already includes search and pagination in `users-card.tsx`.
- Existing unrelated dirty state: `external/agents` submodule is dirty and must not be staged.

## Plan

- Add a small pure filtering helper under `lib/users/` for dormant-only and search composition.
- Add focused Vitest coverage for dormant filtering, search AND logic, and tab-specific search fields.
- Wire `UsersCard` to the helper.
- Add a pill/toggle next to the search input using the existing dark dashboard style.
- Reset filter and pagination on tab changes, and reset pagination when the filter toggles.
- Add distinct empty states for inactive-only matches.

## Risk

Medium: dashboard UI behavior change. No protected paths, auth/permission changes, API changes, migrations, secrets, billing, payments, or production infrastructure.

## Verification Log

- 2026-06-24: Implemented client-side dormant filter and focused helper tests.
- 2026-06-24: `bun node_modules/vitest/vitest.mjs run lib/users/user-management-filter.test.ts` passed with 4 tests.
- 2026-06-24: focused TypeScript check for `lib/users/user-management-filter.ts` and its test passed.
- 2026-06-24: `git diff HEAD --check` passed.
- 2026-06-24: root TypeScript check failed on unrelated baseline error `lib/survey/__tests__/person-name-validation.test.ts(341,22): Cannot find name 'vi'`.
- 2026-06-24: local browser evidence was attempted; unauthenticated `/settings/users` redirected to sign-in and local Turbopack returned a runtime error on the sign-in route. Screenshot saved privately in artifacts.

## Publication Notes

Use `/Users/user/agents/scripts/publish_pr.py`; do not use built-in Codex app commit/push/PR actions.

- 2026-06-24: Erroneous draft PR #1527 on `issue/1458-inactive-user-filter` was closed and the branch was deleted.
- 2026-06-24: Published draft PR #1528 on the existing task branch `feat/add-inactive-1-month-filter-to-user-management-tabs` with commit `22e7c91aa0c2556e4c81b2319e7d1085cca2383d`.
