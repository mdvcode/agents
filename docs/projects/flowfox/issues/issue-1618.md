# Issue 1618 - Landing Page Workspace URL Import

## 2026-06-29
- Classified as medium risk: new user-visible workspace import API/UI with draft persistence, no protected paths.
- Implemented a draft-only URL import endpoint for existing Landing Page Workspace pages.
- Added mapper from scraped URL content into `DraftContent` workspace sections:
  - standard pages: hero/content/social proof/FAQ/CTA
  - order pages v1: `wsOrderConfig` hero/content with packages left manual
- Added `Import from URL` controls to the workspace launcher.
- Added focused Vitest coverage for mapper and route behavior.
- Checks passed:
  - focused Vitest, 4 tests
  - `bun node_modules/typescript/lib/tsc.js --noEmit`
  - `git diff HEAD --check`
  - selected-path security scan and targeted env/secret grep
- Frontend evidence warning: local `/landing-pages` redirects to sign-in without a Supabase session. Screenshot saved privately in artifacts.
