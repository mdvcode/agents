# Tweebit AI Harness by Daryna

## Release-candidate comparison

Status: **local v0.3.0 release candidate in the current working tree; not published**

This document compares the public [`mdvcode/agents`](https://github.com/mdvcode/agents)
baseline at `6871a26c68afefd8f2140ac7da625970dc454304` with the current Tweebit working
tree. The candidate was developed directly by Codex; it was not implemented by submitting this
change as a task to the Harness itself.

Tweebit remains a standalone, local AI Harness. It is not a Chrome extension and it is not a
Codex-only shell. The provider boundary remains independent, although the only runtime adapters
currently shipped are Codex SDK and Codex CLI compatibility.

## Very short version

> The public system is a technical local Agent Control dashboard. Tweebit keeps its execution,
> safety, recovery, and Adaptive foundations, but replaces the crowded main surface with a light
> collapsible desktop sidebar (mobile off-canvas) and four sections: **Создать**, **Задачи**,
> **Статистика**, and **Adaptive Lab**. The focused composer keeps Auto/Adaptive/Fast/Full/Goal visible and accepts up to
> five files including PDF. It stays local and standalone—no Chrome extension, no Codex Projects
> clone or cloud sync. Files are privately validated and attached to the run; both runtimes receive
> bounded text/PDF-text context, while Codex SDK additionally receives revalidated direct and
> scanned images.

## What changed

| Area | Public baseline `6871a26` | Tweebit release candidate |
| --- | --- | --- |
| Product identity | `agent` CLI and the local **Agent Control** dashboard. | User-facing name is **Tweebit AI Harness by Daryna**. The Python distribution and `agent` command keep their compatibility names. |
| Information architecture | Operations, configuration, diagnostics, task intake, and Adaptive evidence compete for attention. | A lightweight collapsible desktop sidebar and mobile off-canvas menu separate **Создать**, **Задачи**, **Статистика**, and **Adaptive Lab**. The composer stays focused; batch and technical controls use progressive disclosure. |
| Task attention | Questions, approvals, active work, history, and system telemetry share the operations surface. | **Задачи** puts actionable items first. Attention is a task filter over backend-authoritative lifecycle data, not a new entity or client-invented state. |
| Operational statistics | Counters, service health, worker state, and task details compete on the same surface. | **Статистика** is a separate full section for counters, service health, and worker state. Task details remain in **Задачи**. |
| Modes | Backend supports `auto`, `adaptive`, `fast`, `full`, and `goal`. | The same five mutually exclusive values are visible in one selector: **Auto / Adaptive / Fast / Full / Goal**. Auto chooses Fast or Full from risk and cannot choose Adaptive until authoritative acceptance is `PASS`; Adaptive is a manual Beta opt-in until then, not an additional checkbox. |
| Adaptive | Efficiency and acceptance diagnostics live in the main technical surface. | **Adaptive Lab** is a separate analytics/evidence section. The execution mode remains named **Adaptive**, and the backend remains authoritative for `PASS`, `FAIL`, and `NOT ENOUGH DATA`. |
| Project/workspace | Work is bound to an initialized and explicitly trusted local Git repository. | The composer selects and summarizes the current initialized project and workspace mode. There is no project catalog, second project authority, Codex Projects clone, filesystem-wide scan, or cloud sync. |
| Attachments | The dashboard has no general file/PDF context intake. | The browser can select, drop, or paste up to **5** files, show removable file chips, upload them to private local staging, and bind an immutable input manifest to the run. |
| Limits | No attachment contract. | Defaults are **100 MiB per file** and **500 MiB per task**. A locally trusted project may raise them to hard ceilings of **512 MiB per file** and **2.5 GiB per task**. The pending-upload pool is capped at **32 sets / 6 GiB** and staging TTL defaults to 24 hours. Runtime text is capped at **120,000 bytes total / 24,000 per reference**; direct image inputs are capped at **10 MiB each / 20 references**. |
| PDF | No dashboard PDF intake. | PDF is included in the first candidate: bounded local inspection, text extraction, and scanned-page rendering are implemented. Encrypted or malformed PDFs are rejected. |
| Consent | No attachment transmission flow. | A task with files requires explicit, per-task consent before intake can continue. The consent is carried in the task envelope and validated by intake/worker boundaries. |
| Runtime context | Text task instructions reach the selected runtime. | The runtime bridge is implemented. Text and extracted PDF text are injected through the common prompt path with 120,000-byte total and 24,000-byte per-reference bounds. Codex SDK receives revalidated direct images and scanned PDF pages as `LocalImageInput`; Codex CLI receives text/PDF text only. More than 20 local-image references are rejected rather than silently truncated. |
| Runtime scope | Provider-neutral runtime contract; Codex SDK production adapter and Codex CLI compatibility adapter are implemented. | The provider-neutral contract remains. No additional model provider is claimed, and the product is not reduced to a Codex-only client. |
| Delivery form | Local CLI plus an authenticated loopback dashboard opened in a normal browser. | Same local form. **No Chrome extension** is built or required. |

## Attachment and PDF boundary

The candidate's implemented local intake supports common text/source formats, PDF, PNG, JPEG, and
GIF. Uploads are streamed into private staging, checked by filename, extension, MIME/signature,
size, and digest, then bound atomically to one run. Archives, executables, path traversal, symlinks,
and active SVG content are rejected. PDF processing is bounded by page, pixel, output, and timeout
limits; text-bearing pages are extracted and scanned pages can be rendered as local images.

Pending uploads share a private pool capped at 32 sets and 6 GiB, so abandoned sequential uploads
cannot grow local staging without bound. The ordinary project limits are 100 MiB per file and 500
MiB per task. Only an explicitly trusted local project configuration may raise them, and never above
512 MiB per file or 2.5 GiB per task. Runtime limits are independent: text injection is bounded to
120,000 bytes total and 24,000 bytes per reference, while each direct image is at most 10 MiB and one
task may expose at most 20 image references.

This intake pipeline and the runtime context bridge are separate trust boundaries, and both are in
the working tree. A task proceeds only after explicit per-task consent. Before every runtime use, the
bridge rechecks the authoritative run path, manifest digest, file descriptors, symlinks, traversal,
sizes, and content digests. Text/PDF-text excerpts are framed as untrusted data, never instructions.
Codex SDK additionally receives at most 20 verified local images; a larger direct/scanned image set
fails closed. Codex CLI does not receive image inputs.

## Scope decisions

Included in this candidate:

- local standalone Harness operation;
- lightweight collapsible desktop navigation and an accessible mobile off-canvas menu;
- four focused sections: **Создать**, **Задачи**, **Статистика**, and **Adaptive Lab**;
- attention as a filter over authoritative tasks rather than a separate client-side entity;
- a full **Статистика** section for counters, service health, and worker state, separate
  from task details and Adaptive efficiency analysis;
- visible Auto, Adaptive, Fast, Full, and Goal selection;
- a separate **Adaptive Lab** evidence surface without renaming Adaptive execution mode or changing
  backend verdict ownership;
- selection and summary of the current trusted project/workspace rather than a project catalog or
  second project database;
- five-file local intake with large bounded limits and PDF in the first release candidate;
- explicit per-task attachment-runtime consent in UI, API, task envelope, and worker validation;
- bounded text and PDF-text runtime context for Codex SDK and Codex CLI;
- revalidated direct/scanned image context for Codex SDK, with a fail-closed 20-image bound;
- the existing approval, retry, recovery, worktree, and publication safety model.

Not included:

- a Chrome extension;
- a Codex Projects clone or Codex/cloud synchronization;
- a standalone project catalog or filesystem-wide project discovery;
- cloud hosting, shared accounts, or multi-user collaboration;
- a continuing chat that silently changes a run after launch;
- additional production model providers;
- archive extraction, Office-document parsing, or OCR;
- image input through the Codex CLI compatibility adapter.

## Safety boundaries that remain

- `agent dashboard` remains loopback-only and always creates a fresh ephemeral bearer token. The
  lower-level control plane without `AGENT_CONTROL_PLANE_TOKEN` is read-only and rejects every POST
  mutation; enabling mutations requires the exact bearer token.
- `.agent/project.yaml` remains the local execution identity; selecting a repository, mode, or file
  does not grant trust or additional authority.
- Attachments are untrusted context. They cannot override the user's request, repository
  instructions, approval policy, or system rules.
- Files and derived context must not appear in public output, metrics, summaries, or raw traces.
- Network, credentials, protected paths, push, merge, publication, and deployment retain their
  existing scoped approval rules.
- Merge and deployment remain human actions.

## Local release and rollback state

The candidate is still a dirty working tree, not a tagged or published release. Before it is called
final, the complete regression suite, security/diff checks, installed-package check, and live desktop
and mobile dashboard check must pass against one recorded revision. Focused attachment-context tests
cover text/PDF text, image input construction, byte/reference limits, consent, digest tampering,
symlinks, traversal, and non-authoritative manifest paths; the final full-suite result is recorded at
release cut.

The current local workflow remains:

```sh
agent update --source /absolute/path/to/reviewed/checkout
agent doctor --full
agent dashboard --repo /path/to/trusted/project
```

For code rollback, install a reviewed checkout of the pre-Tweebit local reliability baseline
`275fc71f29457a0d96d91e31090775a81a53eadb`. A source rollback does not undo tasks, branches,
worktrees, run state, or external actions; state/schema compatibility must be checked before any
downgrade. **Drain or explicitly cancel every queued attachment task before installing an older
worker that does not understand the attachment envelope fields.** Preserve any run inputs needed for
review before cancellation or cleanup.

## Release evidence to record at cut

| Item | Required final evidence |
| --- | --- |
| Release revision | Clean local commit or tagged revision, compared with `6871a26`. |
| UI | Final desktop/mobile screenshots and keyboard/accessibility smoke check. |
| Attachments/PDF | Limits, cleanup, rejection cases, PDF extraction/rendering, rollback, and capability tests. |
| Runtime context | Focused evidence for bounded text/PDF-text prompt context, SDK local-image input, fail-closed image limits, consent, and path/digest revalidation. |
| Regression | Full check, security scan, diff hygiene, installed-package smoke test, and local launch. |
| Rollback | Reviewed state/schema impact and successful source reinstall procedure. |
