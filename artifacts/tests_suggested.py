"""
Flowfox issue 1138 test generator notes.

No real test file was added. The behavior change is a narrow Tailwind layout
constraint in a dashboard component; a unit test would mostly assert className
strings and provide weak regression value. Verification is covered by
TypeScript, targeted ESLint, whitespace checks, and frontend evidence attempt.

Suggested manual/browser coverage when authenticated seed data is available:
- Open /campaigns/{campaignId}/landing-pages/{docId} with walkthroughVideoUrl.
- Confirm the Walkthrough-Video player is capped to a compact width on desktop.
- Confirm controls, regenerate buttons, and the URL link still work.
- Open a document without walkthroughVideoUrl and confirm the empty state is
  compact.
- Check mobile width for overflow.
"""
