# Issue 1584

## Summary
- Add rolling 30-day Umsatz and Leads SOLL / IST columns to User Management Kunden.
- Keep customers sorted by Umsatz descending from the API.
- Do not change Mitarbeiter or Affiliates behavior.

## Constraints
- Flowfox profile.
- Medium risk.
- No migrations, auth/permission changes, production credentials, deploy, merge, or force-push.
- Publication must use `scripts/publish_pr.py`.

## Execution Log
- 2026-06-26T09:40:46Z: Read control-plane policy, Flowfox rules, privacy notes, issue context, target API/UI files, and relevant metric helpers.
- 2026-06-26T09:40:46Z: Planned narrow helper + API + UI + focused tests.
- 2026-06-26T09:53:23Z: Implemented customer KPI helper, API enrichment/sort, Kunden-only table columns, and focused tests.
- 2026-06-26T09:53:23Z: Checks passed: focused Vitest, TypeScript, diff check, targeted ESLint with one existing warning.
- 2026-06-26T09:53:23Z: Frontend target evidence unavailable: unauthenticated redirect to sign-in, then local Turbopack runtime error on sign-in. Draft PR required if publication succeeds.
- 2026-06-26: Correction: closed/deleted incorrect temporary branch `issue/1584-user-management-kpis` and PR #1599. Moved commit to existing working branch `feat/add-30-day-revenue-and-leads-targetactual-columns-to-customers-tab-in-user-management` and opened draft PR #1600.
- 2026-06-26: User provided local Chrome visual evidence: Kunden tab has KPI columns and Umsatz-desc order; Affiliates and Mitarbeiter tabs are unchanged. PR #1600 marked ready.
