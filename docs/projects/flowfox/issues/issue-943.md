# Issue 943: Remove Tools & Utilities Information Banner

## Summary

- Removed the static bottom information banner titled `Über Tools & Utilities` from the Toolbox page.
- Kept the main Tools & Utilities page header, tool cards, permission filtering, pin controls, and menu personalization button intact.
- Added durable agent guidance requiring UI-removal issues to search exact visible text plus neighboring copy/link/icon markers before approval handoff.

## Investigation

- `external/` is not present in this checkout; agent context came from `agents/`.
- GitHub issue 943 says the same element may appear on multiple dashboard/platform views and must be removed everywhere.
- Code sweep found the exact banner title and neighboring explanatory copy in `app/(dashboard)/tools/ToolsBoardClient.tsx`.
- Related `Tools & Utilities` page title/access messages remain because the issue targets the informational banner container, not the page identity or navigation label.

## Implementation

- Deleted the banner wrapper containing:
  - `lightbulb` icon
  - `Über Tools & Utilities` heading
  - explanatory copy about development/testing/debugging
  - link to `Einstellungen → Menü personalisieren`

## Verification

- PASS: zero-match text sweep for removed banner title, explanatory copy, and banner link text.
- PASS: targeted ESLint for `ToolsBoardClient`.
- PASS: root TypeScript check.
- PASS: `git diff --check`.
- PASS FALLBACK: changed-file secret marker scan.
- BLOCKED TOOL MISSING: `detect-secrets` is not installed.
- BLOCKED BASELINE: `bun audit` reports 105 existing advisories, including 2 critical.
- PASS VISUAL: authenticated local `/tools` screenshot confirms the page header and tool cards render while removed banner title/copy/link text are absent.
- EVIDENCE: authenticated screenshot saved at `/private/tmp/flowfox-issue-943-tools-authenticated-final.png`.

## Approval Gate

- Stop before publish and wait for explicit user approval of the verified state.
- Do not commit, push, or create/update a PR until the user explicitly approves the verified state.
- After approval, stage only approved public Flowfox project files and publish with normal product/engineering wording.
