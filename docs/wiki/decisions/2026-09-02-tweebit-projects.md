# Tweebit local Projects and Codex workspace handoff

Date: 2026-09-02
Status: accepted for the v0.4 local candidate

## Decision

Tweebit exposes a first-class local **Projects** catalog backed by the existing private trust
registry created by `agent init`. One Harness project maps to one canonical primary repository in
this release. The filesystem and `.agent/project.yaml` remain execution sources of truth; the
catalog does not copy repository contents and does not scan the computer for projects.

Every new task records an explicit project key and project id in addition to the legacy execution
profile field. A task belongs to exactly one project. The global **Tasks** section and a project
detail view render the same queue/run records with different filters; neither creates a second task
store.

## Identity boundaries

- `project_key`: SHA-256 key of the canonical registered repository path; local catalog identity.
- `project_id`: human-readable project slug; not globally unique by itself.
- `project`: legacy execution-profile/policy value retained for compatibility.
- `task_id`: user-facing request identity within a project.
- `run_id`: authoritative Harness workflow lifecycle.
- Codex `thread_id`: runtime continuation handle owned by a run, not project identity.
- checkout path and branch ownership: authoritative workspace writer boundary.

Existing tasks without explicit project fields remain readable and are associated by canonical
repository path. New idempotency keys use `project_key` while intake recognizes matching legacy
keys during the compatibility window.

## Codex integration

Harness and Codex interoperate through the same trusted local folder, Git repository, worktrees,
and `AGENTS.md`. **Open in Codex** launches the supported Codex desktop workspace command with an
argument list and no shell. The control plane resolves the folder from the trusted project key; a
browser-provided arbitrary path cannot reach this launch boundary.

Tweebit does not inspect or mutate private Codex application databases, does not claim to register
native Codex sidebar projects, and does not claim cloud synchronization. Exact interactive-thread
handoff remains a later compatibility-gated capability because Harness and an interactive Codex
session must never write the same checkout concurrently.

## Project context, memory, skills, and tools

The v0.4 navigation does not add separate top-level **Memory**, **Skills**, or **Tools** sections.
They are different mechanisms and presenting all three as peer destinations would overload the
interface and incorrectly imply a unified self-learning memory. Project detail may show one compact,
read-only **Project context** summary:

- **Memory/knowledge** describes the sources available to Context Engine and run continuity. In this
  release that includes instructions, selected documentation/wiki/ADR sources, role-scoped run
  artifacts, the run's non-ephemeral Codex thread, and consented task attachments. Dedicated
  `docs/**/memory` files are not advertised as indexed because the production Context Engine does
  not yet include them consistently.
- **Skills** are global, versioned Harness playbooks selected by role and relevance. They are
  instructions, not executable abilities and not project memory.
- **Tools/capabilities** are declared per role and constrained by policy, filesystem scope, sandbox,
  approval, and Harness-owned action boundaries. They are permissions, not knowledge.

The task context compiler keeps `project_id` as display/query metadata, uses the repository-bound
`project_key` to isolate its cache, and reads new private project knowledge only from
`docs/projects/by-key/<project_key>/`. The legacy `project` execution-profile value remains separate;
unkeyed legacy knowledge folders are not shared with project-aware runs. A later context inspector
should be derived from existing manifests and policies rather than creating another mutable
database. Editing, "remember this", project-local Skills, permission changes, Obsidian enrollment,
and memory promotion remain deferred until provenance, owner, freshness, retention, privacy, and
enforcement semantics are visible and testable.

## Security and privacy

- Project catalog and detail endpoints require the dashboard bearer token even when generic
  lower-level GET endpoints are configured read-only.
- Only explicitly registered entries are enumerated; no filesystem-wide discovery occurs.
- Entries expose project metadata and aggregate counts only, never source contents, transcripts,
  prompts, credentials, attachment contents, or raw runtime events.
- Missing, invalid, or stale-fingerprint projects stay visible for repair but cannot launch tasks.
- Passing both project key and repository path requires an exact canonical match.
- Dashboard CLI subprocesses run from the trusted Harness root in isolated Python mode, not from a
  newly selected repository.
- Opening a project in Codex with unfinished Harness work requires an explicit confirmation checked
  by the server as well as the UI.
- Archival or UI selection never deletes repositories, tasks, worktrees, or run evidence.

## Deferred

- Multiple primary repositories in one Harness project.
- Native Codex sidebar/project synchronization.
- Cross-device or cloud project synchronization.
- Simultaneous Harness and interactive-Codex writers in one checkout.
- Rich thread history and approvals through Codex App Server.
- Tweebit MCP/plugin controls inside Codex.
- Editable curated project memory and project-local Skills/Tools policy.
