# Report

## Summary

Implemented Flowfox issue 897. Landing Page Sanity Studio documents now share the Advertorial-style footer layout with `View Live` / `View Preview` next to `Publish`, route to `/l/<slug>`, use `/api/preview` for draft/autosave states, and disable the action when no slug is available.

## File Inventory

### Added

- `docs/issues/issue-897.md`
- `external/agents/docs/projects/flowfox/issues/issue-897.md`

### Modified

- `AGENTS.md`
- `apps/studio/components/AdvertorialViewAction.tsx`
- `apps/studio/lib/advertorial-view-url.ts`
- `apps/studio/sanity.config.tsx`
- `lib/__tests__/advertorial-view-url.test.ts`
- `artifacts/*`
- `external/agents/AGENTS.md`
- `external/agents/artifacts/*`

### Deleted

- None.

## Verification

- PASS: focused Vitest for Studio view URL helper behavior.
- PASS: targeted ESLint for changed Studio action/config/helper/test files.
- PASS: root Bun TypeScript check.
- PASS: Studio Bun TypeScript check.
- PASS: `git diff --check`.
- PASS: `detect-secrets scan <changed safe files>`.
- BLOCKED BASELINE: `bun audit` reports 105 existing vulnerabilities, including 2 critical.
- PASS: `make check` and `make security` in `external/agents`.

## Next Action

- Review and manually verify the Studio footer layout before rollout.
