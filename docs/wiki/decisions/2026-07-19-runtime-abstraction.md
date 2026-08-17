# Runtime Abstraction

Date: 2026-07-19

Status: runtime boundary remains active; the CLI-only production selection was superseded by `2026-08-15-python-codex-sdk-runtime.md`.

## Decision

The Harness manages runtimes; it does not embed a model provider.

```text
Harness
  -> Runtime.execute(role, context, task, worktree, artifacts)
  -> Runtime Adapter
  -> provider transport
```

All model-backed role execution and runtime preflight pass through the provider-neutral `Runtime` contract. Harness code may inspect only the runtime descriptor and result contract. It must not construct provider commands, call provider SDKs, or import provider-specific preflight/execution helpers.

## Step 2 scope

Step 2 has exactly one production runtime:

- provider: `codex-cli`;
- transport: local subscription;
- execution: local `codex exec` through `CodexCliRuntime`;
- API requirement: false;
- Model Router: disabled.

`.agent-runtime.yaml` is the runtime configuration authority. `.agent-workflows.yaml` selects `codex-cli` by provider name and does not contain a provider adapter command. `scripts/runtimes/registry.py` constructs the runtime. `scripts/agent_role_runner.py` calls only `Runtime.preflight(...)` and `Runtime.execute(...)`.

`test-subprocess` is a non-production compatibility fixture for deterministic tests. It cannot be selected as a production provider in `.agent-runtime.yaml`.

## Provider adapter responsibility

`CodexCliRuntime` owns provider-specific behavior: Codex availability/authentication preflight, invocation of the structured Codex executor, result collection, trace provenance, and token/duration evidence. The generic subprocess base owns process safety and contract validation, not Codex semantics.

## Deferred roadmap

- Step 3 may add independent `OpenAIAdapter`, `ClaudeAdapter`, and `OllamaAdapter` implementations behind the unchanged `Runtime` contract.
- Step 4 may add a Model Router that selects among registered runtimes.

Adding a provider or routing policy before those steps is out of scope. Existing Harness, deterministic gates, workflow state, repair loops, and publication logic must remain unchanged when a future adapter is introduced.

## Consequences

- The current subscription-based Codex CLI path does not create an OpenAI API dependency.
- Provider changes are localized to a runtime adapter and registry entry.
- Runtime identity, transport, command boundary, production flag, and API requirement are saved in `workflow.json`; raw events include provider provenance.
- Architecture tests reject direct Codex/OpenAI/Anthropic execution dependencies in the Harness.
