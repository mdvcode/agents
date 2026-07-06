# Issue 1586 — Return To Origin Page After Ending Admin Impersonation

## Intake
- GitHub issue: https://github.com/PrimestSpec/flowfox/issues/1586
- Branch: `fix/return-to-origin-page-after-ending-admin-impersonation-instead-of-settings-overview`
- Profile: `flowfox`
- Risk: HIGH because the patch touches admin impersonation/session-flow behavior.

## Implemented Patch
- Added `sanitizeAdminImpersonationReturnTo()` in `lib/impersonation.ts`.
- Added optional `returnTo` to `ImpersonationStash`.
- Start API accepts optional `returnTo`, sanitizes it, and stores `/settings` fallback.
- Stop API uses stored `returnTo`, including absolute main-app redirects from portal hosts.
- `ImpersonateUserAction` sends current path/query by default or explicit `returnTo`.
- User Management syncs `tab`, `page`, `q`, and `inactive` to query params and passes that path into impersonation starts.

## Tests And Checks
- PASS: focused Vitest, 37 tests.
- PASS: root TypeScript check.
- PASS: `git diff HEAD --check`.
- PASS with warning: targeted ESLint, one existing-style hook dependency warning.
- Security grep reported baseline env variable references outside the task diff.

## Frontend Evidence
- Local server required bundled modern Node because system Node is too old for Next.
- Turbopack local attempt hit a Next panic on `/authentication/sign-in`.
- Non-Turbopack local server redirected unauthenticated `/settings/users?tab=customers&page=2&q=acme` to `/authentication/sign-in` and rendered sign-in.
- Screenshot: `/Users/user/agents/artifacts/flowfox-issue-1586-auth-redirect-nonturbo.png`.
- Full impersonation round trip still needs authenticated admin/staging verification.

## Publication
- No commit, push, PR, or publish script was run.
- Reason: HIGH risk requires human approval first.

## Next
After approval, run authenticated staging verification and then the required `publish_pr.py --dry-run` gate before any publication.
