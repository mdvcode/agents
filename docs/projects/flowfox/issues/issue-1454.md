# Issue 1454: Remove duplicate Benutzerverwaltung heading

## Summary

Remove the duplicate `Benutzerverwaltung` title/subtitle from the User Management card on `/settings/users`. Keep the standard `PageHeader` as the single page title.

## Scope

- Project profile: `flowfox`
- Risk: LOW
- Changed application file: `app/(dashboard)/settings/users-card.tsx`
- Protected areas: none touched

## Implementation Notes

- Removed the card-local title/subtitle block from `UsersCard`.
- Left tab switching, role badge, search, pagination, fetches, modals, and row actions unchanged.
- Kept `UsersManagementClient` and the canonical `PageHeader` unchanged.

## Verification

- `git diff HEAD --check`: passed.
- `bun node_modules/typescript/lib/tsc.js --noEmit`: failed in unrelated `lib/survey/__tests__/person-name-validation.test.ts` with `TS2304: Cannot find name 'vi'`.
- Local browser: `/settings/users` redirects to `/authentication/sign-in` without an authenticated session, so full visual evidence of the fixed dashboard view is unavailable.

## Publication Notes

- Publish as draft due to unavailable authenticated visual evidence and unrelated root TypeScript failure.
- Publication must use branch `fix/remove-duplicate-benutzerverwaltung-heading-on-user-management-page`; final task diff against `origin/main` contains only the duplicate heading removal.
- Do not include raw issue text, private screenshots, or private artifact paths in public PR text.
