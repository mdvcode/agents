# Goal

## GOAL

- Implement Flowfox issue 943 by removing the `Über Tools & Utilities` information banner from the Toolbox UI and verifying it is not rendered elsewhere.

## CONTEXT

- GitHub issue 943 asks to remove a static informational banner from the Toolbox and any other dashboard/platform views where the same element appears.
- User note says the element may exist on multiple pages and should be found quickly through code search.
- `external/` is not present in this checkout; agent context is under `agents/`.
- Existing Flowfox agent rules already require local screenshot/video evidence before approval and commit/push/PR only after explicit user approval.

## CONSTRAINTS

- Keep the `/tools` page, grid cards, pin controls, and header action intact.
- Remove the banner container from the DOM, including lightbulb icon, explanatory copy, and link to menu preferences.
- Do not touch migrations, auth, billing, payments, secrets, dependencies, or production infrastructure.
- Do not commit, push, or create a PR before explicit approval of the completed state.
- Do not publish private agent/control-plane files.

## RISK

- LOW: narrow presentation-layer removal in one dashboard client component.

## PLAN

1. Read Flowfox agent rules, privacy guidance, and existing lessons.
2. Fetch and summarize issue 943.
3. Search exact banner title, neighboring copy, link text, and icon/info markers across UI code.
4. Remove the banner element with a minimal patch.
5. Update private agent issue journal and process artifacts.
6. Run focused verification: zero-match text sweep, targeted ESLint, TypeScript, diff check, and security/dependency checks where available.
7. Start/reuse local dev server and capture local screenshot evidence for `/tools`.
8. Stop at `await_approval` with evidence, changed files, checks, risk, and blockers.

## DONE WHEN

- `Über Tools & Utilities` has zero UI-code matches.
- The bottom banner is absent from the local `/tools` page screenshot.
- Checks pass or blockers are recorded.
- Private agent artifacts and issue journal are updated.
- Next action is explicit approval before commit/push/PR.

## VERIFY

- `rg -n "Über Tools & Utilities|Diese Seite sammelt verschiedene Tools|Einstellungen → Menü personalisieren" app components lib -S -g '!lib/generated/**'`
- `bun node_modules/eslint/bin/eslint.js app/(dashboard)/tools/ToolsBoardClient.tsx`
- `bun node_modules/typescript/lib/tsc.js --noEmit --incremental false`
- `git diff --check`
- `detect-secrets scan <changed safe files>` or fallback marker scan if unavailable
- `bun audit`
- Local browser screenshot of `/tools`
