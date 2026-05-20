# Performance Optimization Skill

## Purpose
Find safe complexity or performance improvements without changing behavior prematurely.

## Default Mode
Report-only.

## Report Fields
- File and line.
- Current complexity or cost.
- Recommended change.
- Expected complexity or cost after change.
- Risk level.
- Tests or benchmarks required.

## Rules
- Do not patch during broad analysis unless the user explicitly asks.
- Prefer low-risk local improvements.
- Preserve behavior and public APIs.
- Add tests or benchmarks before claiming improvement.
