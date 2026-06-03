# FlowFox Graph

- `lib/cms.ts` is the final Sanity write helper for CMS document creation.
- `apps/studio/schemas/types/advertorial.ts` defines Sanity advertorial editor fields.
- `app/api/ai/cms/generate-async/route.ts` handles legacy async CMS generation.
- `app/api/ai/advertorials/workspace-generate/[campaignId]/route.ts` creates draft advertorials.
- `app/api/ai/advertorials/workspace-generate-stream/[campaignId]/route.ts` streams draft advertorials.
- `app/api/ai/advertorials/workspace-keep/route.ts` persists workspace drafts into Sanity.
