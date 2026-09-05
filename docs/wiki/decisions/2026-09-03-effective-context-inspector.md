# Decision: Effective Context revision and Inspector evidence

Date: 2026-09-03

## Status

Accepted as the first stabilization slice for the Context Intelligence Platform.

## Context

The Context Engine already produced one bounded package with selected/excluded provenance. Cache compatibility, however, considered only sources selected by the previous build. A newly discovered relevant source, or a changed source outside the target repository, could therefore leave an older package reusable. The manifest also lacked a first-class digest of the exact runtime payload and normalized privacy/trust fields for an Inspector client.

## Decision

- Compute `context_revision` from the complete discovered source inventory after collection, including source identity, content fingerprint, classification metadata, and compiler/policy/project-profile versions.
- Require both exact and compatible cache entries to match the current revision before reuse.
- Compute `effective_context_digest` from the exact compiled package and write it into the provenance log and context manifest.
- Add a versioned `context_inspector` manifest view derived from the same selected/excluded arrays used by the compiler.
- Mark policies, contracts, skills, project profiles, and authoritative run artifacts as trusted. Mark repository documentation and other reference material as `untrusted-reference`; reference content does not gain instruction authority.
- Keep the existing `ContextEngine.build(task, repository, role, runtime)` API and existing manifest fields intact.

## Consequences

- Context discovery still runs before cache reuse, trading some lookup speed for correctness; retrieval and package construction remain cacheable.
- Adding, removing, reclassifying, or changing a discovered source invalidates cached Inspector/package evidence.
- Existing manifest consumers remain compatible because prior fields are unchanged and new fields are additive.
- The backend now exposes the evidence needed by a real Context Inspector UI without introducing a second preview compiler.
- Attachment/PDF ingestion, page provenance, secret filtering, privacy destination enforcement, and the Project AI Harness Builder are intentionally deferred.
