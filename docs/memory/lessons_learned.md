- Date: 2026-04-16
  Agent: Codex
  Failure: Contact-specific trigger logic had spread into small free helper functions and a dedicated post-save hook.
  Root cause: The implementation optimized for local convenience instead of the repository preference for behavior living on the owning model.
  Prevention rule: If logic mainly operates on one `ClickfunnelsContact`, implement it as a member method on `ClickfunnelsContact` unless there is a strong cross-model reason not to.
  Example bad pattern: `contact_is_sellable_for_outgoing_custom_conversion_trigger(contact)` plus a separate signal wrapper.
  Example good pattern: `contact.can_be_transferred_for_outgoing_custom_conversion_trigger()` called from the model lifecycle point that already owns the business transition.
  Scope: repository-wide

- Date: 2026-04-16
  Agent: Codex
  Failure: The same business event (`ContactCanBeTransferred`) was wired into multiple lifecycle points and could fire more than once for one contact.
  Root cause: The implementation followed technical state transitions like verify-phone success instead of choosing one canonical business event boundary.
  Prevention rule: For event-style tracking, choose one canonical lifecycle point and route all paths through it instead of firing from intermediate status updates plus final action attempts.
  Example bad pattern: firing once on phone-validation success and again on the first transfer attempt.
  Example good pattern: fire only from `TransferMethod.transfer_contact_impl()` when the first real transfer attempt begins.
  Scope: repository-wide

- Date: 2026-04-20
  Agent: Codex
  Failure: A clean modern runtime exposed multiple undeclared startup dependencies that old long-lived virtualenvs had been masking.
  Root cause: The repository relied on historical environment drift instead of declaring every direct runtime dependency needed during Django startup and app import.
  Prevention rule: When upgrading Python or Django, validate startup from a fresh isolated venv and add every direct startup/runtime dependency explicitly to the target requirements file instead of assuming the old environment contents.
  Example bad pattern: code imports `inflection`, `psutil`, `fasteners`, `facebook_business`, `openai`, `pygsheets`, or `django_redis` during startup without pinning them in the runtime requirements.
  Example good pattern: declare direct startup/runtime dependencies explicitly in the target requirements set and verify `manage.py check`, `manage.py shell`, and `runserver` from a clean venv.
  Scope: repository-wide

- Date: 2026-04-22
  Agent: Codex
  Failure: A management command started timers in `__init__()`, so even `manage.py <command> --help` could hang or need external termination.
  Root cause: Runtime guard setup was tied to command construction instead of actual command execution.
  Prevention rule: For Django management commands, keep `__init__()` side-effect free; start timers, threads, signal handlers, and long-lived guards only inside `handle()` or a method called from it.
  Example bad pattern: constructing `Timer(...)` objects and starting them inside `Command.__init__()`.
  Example good pattern: initialize plain attributes in `__init__()` and arm timers only when `handle()` begins.
  Scope: repository-wide

- Date: 2026-04-22
  Agent: Codex
  Failure: Upgrading a Django app package on py311 without aligning the legacy table schema broke admin views because the model started selecting columns that do not exist in the database yet.
  Root cause: Package/model upgrades outpaced schema alignment on a legacy database, and admin code assumed the full modern model field set always exists physically.
  Prevention rule: When validating py311 against legacy databases, compare model fields with real table columns for third-party apps before assuming admin/model surfaces are safe; prefer narrow compatibility guards over autonomous schema mutation when the task is just to restore the test stand.
  Example bad pattern: upgrading `django_celery_results` and letting admin query `periodic_task_name` or `date_started` against an older TaskResult table.
  Example good pattern: detect missing DB-backed fields dynamically in admin/query surfaces, restore functionality, and schedule schema alignment as a separate deliberate task.
  Scope: repository-wide

- Date: 2026-04-23
  Agent: Codex
  Failure: A broad admin sweep initially produced unusable results because noisy startup prints from Django app `ready()` methods polluted machine-readable stdout.
  Root cause: The sweep harness assumed exclusive control of stdout even though the application imports emit legacy debug prints during startup.
  Prevention rule: When building automation against this repository, expect import-time stdout noise; either parse the last JSON object from output or isolate structured output onto a dedicated stream or file.
  Example bad pattern: calling `json.loads(completed.stdout)` directly and assuming it contains only JSON.
  Example good pattern: extract the final JSON line or write structured output to a separate file while tolerating startup logs.
  Scope: repository-wide

- Date: 2026-04-23
  Agent: Codex
  Failure: A single pathological admin changelist can hang a whole sweep if all models are checked in one long-lived process.
  Root cause: Several legacy admin surfaces perform enough work during GET rendering that per-request timeouts are not sufficient to protect the whole run.
  Prevention rule: For large admin sweeps on this repository, isolate each model probe in its own subprocess with a hard wall-clock timeout so one legacy page cannot block the full validation pass.
  Example bad pattern: iterating every admin model inside one Django process and relying on soft request-level timeouts only.
  Example good pattern: spawn one child process per model, enforce a hard timeout, log progress incrementally, and keep the overall run moving.
  Scope: repository-wide

- Date: 2026-04-24
  Agent: Codex
  Failure: The first server-side URL sweep misclassified every safe probe as a failure because the py311 runtime on the test host could not verify the `contactapi2.static.fyi` certificate chain.
  Root cause: The harness treated TLS trust-store failure as if it were an application route failure.
  Prevention rule: For internal contactapi2 test-stand HTTP sweeps, separate transport/TLS failures from backend failures; use a controlled unverified HTTPS context or a known-good local nginx path before classifying route health.
  Example bad pattern: reporting `CERTIFICATE_VERIFY_FAILED` as 66 backend failures.
  Example good pattern: rerun with explicit internal-test TLS handling, then check HTTP status and uWSGI logs for actual 500/traceback evidence.
  Scope: py311 test-stand sweeps

- Date: 2026-04-27
  Agent: Codex
  Failure: A focused clickfunnels probe initially hung because it used `SavedHttpRequest.path/full_path__icontains` on a large legacy table.
  Root cause: The harness used convenient ORM substring filtering instead of walking recent primary-key ranges and filtering route strings in Python.
  Prevention rule: For large legacy request-log tables, avoid broad `icontains` discovery during py311 sweeps; scan bounded PK batches and only hydrate full rows after selecting candidate IDs.
  Example bad pattern: `SavedHttpRequest.objects.filter(path__icontains="webflow-ajax")[:5000]`.
  Example good pattern: fetch recent `id/path/full_path` rows ordered by indexed primary key, filter in Python, then load only the few matching rows needed for replay.
  Scope: py311 test-stand sweeps

- Date: 2026-08-05
  Agent: Codex
  Failure: The installed `agent` command passed `agent doctor` but the first `agent task` crashed with a raw `ModuleNotFoundError` because the long-lived pipx environment no longer matched the source checkout's declared runtime dependencies.
  Root cause: The health check verified resource paths and the Codex executable, but did not import the same Python modules used by task intake; the CLI also allowed import failures to escape its user-facing error boundary.
  Prevention rule: A readiness command must exercise every lazy import boundary required by the next advertised command, and the command boundary must convert dependency/import failures into a concise cause plus a repair command.
  Example bad pattern: report “ready” after checking only executable paths, then import queue/runtime modules for the first time after task submission.
  Example good pattern: have `agent doctor` import the task runtime dependencies, make `agent start` refuse an incomplete environment, and direct stale installs to the product-level `agent update` command.
  Scope: agent control-plane CLI and packaged runtime

- Date: 2026-08-06
  Agent: Codex
  Failure: A background workflow could preserve a useful role question internally while the queue and CLI showed only generic `blocked` or `approval required`, leaving no safe command to provide the missing information to the same run.
  Root cause: Human attention was treated only as an approval state; role summaries, blockers, informational answers, and authority-granting decisions were not carried as separate end-to-end contracts.
  Prevention rule: Every paused autonomous task must preserve an actionable summary and concrete missing items through workflow, worker, queue, and CLI; informational answers must resume the same checkpoint, while risk/security/publication authority must remain an explicit scoped approval.
  Example bad pattern: retry a role or tell the user only that approval is required, without showing the question or accepting a run-bound answer.
  Example good pattern: print `ATTENTION REQUIRED`, show the exact question and `agent answer <run-id> ...`, then resume the same run with the sanitized answer available to the role.
  Scope: agent control-plane workflow, recovery, and CLI UX

- Date: 2026-08-06
  Agent: Codex
  Failure: First-use instructions unconditionally told users to commit `.agent/project.yaml` and `AGENTS.md`, even when the target repository deliberately ignored both files.
  Root cause: The installer treated clean-checkout readiness as equivalent to tracking local execution configuration in Git.
  Prevention rule: Detect ignored setup files during initialization, accept them as valid local configuration, and never recommend `git add -f`; require only a clean checkout before branch switching.
  Example bad pattern: always print `git add .agent/project.yaml AGENTS.md` after installation.
  Example good pattern: report ignored setup as valid, and tell users with non-ignored new files to either commit them or intentionally ignore them according to project policy.
  Scope: installer, project initialization, and ordinary-user documentation

- Date: 2026-08-06
  Agent: Codex
  Failure: Task intake rejected an existing or generated task branch with the generic message `task branch must be a safe git branch name`, even though Git accepts useful branch characters beyond the Harness's ASCII-only allowlist.
  Root cause: The Harness duplicated Git ref validation with a narrower regular expression and validated generated output after construction instead of making safe construction an invariant.
  Prevention rule: Accept branch names according to Git ref rules, generate bounded task branches safely by construction with a deterministic fallback, and include the offending value plus remediation when a genuinely unsafe explicit ref is rejected.
  Example bad pattern: allow only `[A-Za-z0-9._/-]` and expose a generic validation failure after the user submits a long task.
  Example good pattern: accept Git-valid Unicode and punctuation, normalize generated task identifiers, and keep blocking only ambiguous ref syntax such as `../`, `@{`, repeated `/`, and `.lock`.
  Scope: task intake and local Git workspace selection

- Date: 2026-08-07
  Agent: Codex
  Failure: The installed worker could locate most bundled resources but failed while loading `.agent-recovery.yaml`, so queued tasks stayed unhealthy even though source-checkout tests passed.
  Root cause: Recovery policy discovery derived a repository root from the installed Python module path instead of using the shared Harness resource locator.
  Prevention rule: Every packaged policy, schema, prompt, or workflow resource must resolve through `harness_home()`, and `agent doctor` must load the resource rather than only checking the Harness directory.
  Example bad pattern: `Path(__file__).resolve().parents[2] / ".agent-recovery.yaml"` inside an installed module.
  Example good pattern: resolve `harness_home() / ".agent-recovery.yaml"`, load and validate it in readiness checks, then verify the installed worker after reinstalling.
  Scope: packaged agent control plane and worker startup

- Date: 2026-08-11
  Agent: Codex
  Failure: A paused role could ask the same question again after the user had answered it, creating an unlimited answer/resume cycle with no implementation progress; technical failures could also be presented as answerable questions.
  Root cause: Retry suppression stopped one active workflow process but question identity was not preserved across the answer/resume boundary, and answerability was inferred from broad non-completed statuses.
  Prevention rule: Fingerprint every answerable question across its full run lifecycle, stop a repeated answered fingerprint as an explicit technical blocker, and accept informational answers only for an explicit `awaiting_approval` role question.
  Example bad pattern: classify `blocked`, `failed`, and `awaiting_approval` alike, then reopen a new answer gate whenever the resumed role repeats its summary.
  Example good pattern: carry a stable question id and structured choices, record the fingerprint with the answer, resume the same checkpoint once, and surface any repeated fingerprint as `repeated_question` without creating another approval request.
  Scope: agent control-plane workflow, runtime role contract, CLI, and dashboard UX
