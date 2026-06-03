# Report

## Summary

- Added Flowfox rules that commit messages, PR titles/bodies, issue comments, and release notes must not mention agents, Codex, AI assistance, automation, or private control-plane paths.
- Added hard exclusions so private agent/control-plane files are never staged, committed, pushed, or included in Flowfox PRs.
- Preserved the existing approve-gated workflow and configured git identity requirements.

## Checks

- Passed: `make validate-artifacts`
- Passed: `git diff --check`
- Note: `make` emitted non-blocking macOS sandbox warnings about `/tmp/xcrun_db`, but artifact validation passed.

## Risk

- LOW: private process documentation and prompt guidance only.

## Next Action

- Use this workflow on the next Flowfox issue: implement, verify, capture local visual evidence, request approval, then publish only approved public project files with normal product/engineering commit and PR text.
