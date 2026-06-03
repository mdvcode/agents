# Issue 491: Inconsistent Meta-Information in Advertorial Titles

## Intake
- Source: GitHub issue `primest-spec/flowfox#491`.
- State: open.
- Labels: Medium, Project `<Kampagnen>`, Project `<Advertorials> (Sanity.io)`.
- Request: AI-generated advertorials must consistently show the selected generation setup in the title, e.g. `[Original Title] (Rational & Faktenbasiert 2)`.

## Problem
- Existing code only appended setup metadata for some generated advertorials.
- The likely root cause was variant handling that appended setup only when `variantNumber > 1`, leaving first variants unlabeled.
- Marketers lose CRO context when comparing Rational vs Emotional setups in list views.

## Implementation Notes
- Added shared setup label helper.
- Sanity advertorial documents now support `is_ai_generated` and `generation_setup`.
- New AI generation paths pass setup metadata into Sanity writes.
- List/API projections append display suffixes from metadata when needed.
- Added dry-run backfill script for legacy documents where setup is available from metadata or slug suffix.

## Verification
- Pending:
  - `bun test lib/advertorial/__tests__/generation-setup-title.test.ts app/api/ai/advertorials/__tests__/workspace-generate.test.ts`
  - `bun run typecheck`
  - `git diff --check`
  - agent artifact validation

## PR Notes
- Do not include raw issue screenshot or private issue body in PR.
- Mention that live backfill was not executed.
