# Context Inspector

The dashboard's **Посмотреть глазами AI** action has two views.

- In the task composer it previews sources for the selected role on the current checkout. It does not create a task, start a worker, or send data to a model. Refresh after changing source files. This draft package does not yet include the future run's artifacts, answers, worktree identity or execution settings.
- In a task it opens the frozen input of a particular runtime call. Choose a saved stage, inspect included/excluded sources, and expand the exact prompt and execution contract. Older runs without effective-input snapshots show an explicit unavailable state.

The second view reads the same immutable snapshot used at the SDK/CLI submission boundary. It covers the Harness-supplied prompt, output schema, model settings, sandbox, repository and session identity. It does **not** claim to reproduce runtime-owned system instructions, earlier SDK session turns, provider caches or later tool reads. A prepared snapshot proves the input was frozen before the attempted call, not that a provider accepted or completed it.

## Privacy controls

Markdown knowledge sources can declare a privacy class in YAML frontmatter:

```yaml
---
privacy: local-only
---
```

Supported classes are `public`, `project-private`, `local-only` and `secret-never-model`. The default is `project-private`. Invalid classification is withheld as `local-only`. A reference cannot grant itself instruction authority with a `trust` field.

`local-only` and `secret-never-model` sources are excluded before retrieval, package construction, caching and context logging. They are not sent even to a runtime whose name suggests it is local. A future certified local destination needs its own explicit policy support. These labels govern supplied context; they do not create operating-system access controls over files a coding runtime may later read with tools.

Project-private destinations are controlled by the private Harness `.agent-policy.yaml`, not by reference documents or `.agent/project.yaml`:

```yaml
context_privacy:
  project_private_destinations: [codex-sdk, codex-cli]
  projects:
    example-project:
      project_private_destinations: [codex-sdk]
```

The defaults retain the two existing Codex subscription adapters. An empty list denies model-bound private input. Unknown/new destinations do not gain implicit access. This setting does not install or enable another runtime, and changing it requires the existing policy/approval process.

Credential-like source content is excluded with reason `secret`. Credential-like task descriptions, role inputs, answers or final prompts block sending instead of silently changing instructions. The detector recognizes common provider keys, private-key blocks, credential URLs, bearer values and literal credential assignments. It is a conservative heuristic with possible false positives/negatives, not a general data-loss-prevention guarantee. Context logs redact detected values and record a task fingerprint instead of the full goal. Runtime-generated outputs and subsequent tool access retain their separate existing controls.

## Integrity and storage

- Source inventory changes invalidate context cache reuse; destination and guard version participate in cache identity.
- A changed compiled package is rejected by the adapter. A package exceeding the runtime byte limit is blocked rather than silently truncated after inspection.
- Source packages/manifests use private atomic writes. Effective-input snapshots are stored with mode `0600` under `.agent-runs/<run-id>/context-manifests/effective/`.
- `effective_context_digest` hashes canonical JSON of the final payload; `prompt_digest` hashes its exact UTF-8 prompt; `snapshot_digest` also covers local provenance. Snapshots are content-addressed and never silently overwritten.
- `context_package_digest` names the earlier package hash. For existing manifest consumers, `effective_context_digest` in the **compiler manifest** remains a package-hash alias with `effective_context_scope: context_package`. The **effective-input snapshot** uses `effective_context_digest` for the full final input. Consumers must respect the scope.
- Every attempted role/repair submission records a digest-only entry in `submissions.jsonl`. Structured-output repairs have their own snapshot and do not incorrectly list previous-stage sources as part of the new repair prompt.
- Inspector reads reject invalid digests, corrupted provenance, oversized snapshots, symlinks and path traversal. API responses are uncached and subject to the existing bearer check plus loopback/same-origin restrictions.

## API

- `POST /ui/context/preview`: JSON `repository`, `goal`, optional `role` (default `planner`). Returns a draft source package and provenance; no run is created.
- `GET /runs/<run-id>/context`: available frozen inputs, newest first, up to 200.
- `GET /runs/<run-id>/context/<64-character-digest>`: verified snapshot, including the exact prompt and response schema.

Source preview and stage inspection intentionally have different scope labels. A source preview is not a promise that a future task will have the same final prompt. The exact stage view is the audit artifact for what Harness supplied.

## Verification

`make check` includes privacy, cache invalidation, tamper/path checks, API integration and SDK/CLI input-capture regressions. `make codex-smoke` additionally executes a real read-only SDK role and requires its valid effective-input snapshot. These checks do not substitute for the separate production soak or multi-runtime acceptance gates.
