# Decision: Guarded effective-input boundary and Inspector

Date: 2026-09-05

## Context

The September 3 foundation exposed source provenance and a package digest. Adapters subsequently added role instructions, request metadata, answers and schemas, so the package hash could not describe the final input. Privacy labels were descriptive rather than enforced during context construction.

## Decision

Filter prohibited and credential-like sources before retrieval/cache/logging. Keep destination authority in the private central policy. Scan final role and repair inputs at both Codex submission boundaries, freeze a private content-addressed snapshot, and send its exact prompt/schema. Validate payload and provenance digests when reading Inspector evidence. Retain atomic package writes and block post-inspection truncation or package tampering.

Expose draft source preview in the composer and exact frozen stage inputs in Tasks. Preserve SDK session continuity while explicitly excluding runtime-owned history/system instructions and future tool reads from new-input claims. A snapshot's prepared state is not provider-completion evidence.

## Consequences

Existing package-hash consumers keep an explicitly scoped compatibility alias. Older runs cannot retroactively obtain exact input evidence. Secret detection remains heuristic, and source privacy labels do not replace filesystem sandbox policies for later runtime tool reads. Attachments, Blueprint/export and additional providers remain separate delivery slices.

See `docs/context-inspector.md` for controls, storage and API details.
