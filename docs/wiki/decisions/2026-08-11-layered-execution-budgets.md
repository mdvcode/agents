# Layered Execution Budgets And Explicit Long Goals

Date: 2026-08-11

## Decision

The Harness treats task duration as several independent controls rather than one timeout:

- a model-backed role executor has a 30-minute emergency cap;
- `fast` bounds the complete short workflow to 15 minutes;
- `full` bounds an ordinary complete specialist workflow to 60 minutes;
- `goal` is an explicit checkpointed long objective with a 4-hour cap;
- iteration, role-count, token, stuck/no-progress, automatic-recovery, queue, and human-attention limits remain separate.

Automatic routing may select `fast` or `full`, but never `goal`. Existing `fast` and `full` names remain stable for API and queue compatibility. The CLI and dashboard explain the limits in user-facing labels.

## Basis

The design follows the control dimensions used by established coding agents rather than copying one vendor timeout:

- [Codex durable goals](https://learn.chatgpt.com/codex/use-cases/follow-goals) are explicit, success-condition-driven, checkpointed work intended to run for many hours, distinct from an ordinary task.
- [Claude Code](https://code.claude.com/docs/en/cli-usage) exposes turn and monetary-budget caps independently and does not impose a default turn cap in non-interactive mode.
- [GitHub Copilot's cloud coding agent](https://docs.github.com/en/enterprise-cloud@latest/copilot/concepts/agents/cloud-agent/about-cloud-agent) publishes a 59-minute maximum execution window, which provides a useful scale for an ordinary remote session.
- OpenHands separately exposes [maximum iterations](https://docs.openhands.dev/openhands/usage/environment-variables) and [stuck detection](https://docs.openhands.dev/sdk/guides/agent-stuck-detector) for repeated action/observation, repeated error, monologue, and alternating-cycle patterns.

These products do not publish one common “correct” timeout. The 60-minute ordinary cap is therefore a local operational choice informed by the Copilot session boundary; the 4-hour goal cap is a bounded local realization of explicit multi-hour goal work.

## Consequences

- An ordinary task can no longer run for two hours merely because it selected the full specialist route.
- Long work remains available but is visible and intentional at task submission.
- A role timing out does not falsely imply that the whole task consumed its full session budget.
- The worker's 4-hour outer timeout is meaningful as the long-goal ceiling and an emergency failsafe; shorter mode budgets apply first.
- Monitoring can report which bound was reached instead of describing every stop as a generic timeout.
