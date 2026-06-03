# FlowFox Scratchpad

## Issue 491
- Root cause candidate confirmed in code: setup suffix was appended only for `variantNumber > 1`.
- Backfill should be dry-run first; only patch documents with existing `generation_setup` or recognizable slug suffixes such as `-rat2`.
