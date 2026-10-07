# Result acceptance and comparison

Execution completion and user acceptance answer different questions. Completion says the workflow
returned a result. Acceptance records whether the reviewed result satisfies the user's goal.

## Dashboard example

1. Choose a project, leave **Модель → По настройкам системы**, and select Fast or Full.
2. Describe the result, for example: “Export empty values as empty CSV cells. Preserve quoting.
   Add a regression check. Local only. Do not publish.”
3. After completion, open **Задачи → Посмотреть результат**. The viewer shows the saved summary,
   changed-file names, quality/security/review status, and a pull-request link when available.
4. Choose **Принято** if it meets the goal. Otherwise choose **Нужны правки** or **Отклонено** and
   explain why. **Подготовить задачу для правок** creates a draft; it does not launch work.
5. Open **Статистика** to inspect assessments, elapsed time, tokens, automatic repairs, and actual
   recorded user interventions. Unreviewed results do not count as accepted.

Acceptance never grants approval, publication, merge, deployment, or retry authority. A comment
does not silently resume the old workflow. These operations retain their existing policy gates.

## Revision identity and privacy

Feedback is appended to the run's private `result-acceptance.jsonl` with bounded text, an actor,
timestamp, sequence and result fingerprint. The fingerprint binds the base commit, changed content,
workflow outcome and required check artifacts. Saving from an outdated viewer returns a conflict:
the user must inspect the changed result before assessing it. An identical-content commit does not
invalidate an assessment. A stored snapshot retains the historical assessment when its checkout is
no longer available; historical-only assessments are shown separately from current acceptance.

The loopback API exposes `GET/POST /runs/{run_id}/result-acceptance` only with the dashboard's bearer
token, loopback host and same-origin access. POST also requires an enabled mutation server.
The write contract is `{status, reason, expected_fingerprint}`; the dashboard actor is assigned by
the server. Ordinary metrics carry only a compact assessment badge on authenticated same-origin
requests; they never expose comments or result snapshots. Tokenless metrics omit the new fields.
No private feedback or execution memory belongs in public project output.

## Reading the comparison

Fast and Full cohorts describe saved runs, with each measurement's sample count and missing count.
The current acceptance rate divides accepted current assessments by all current assessments.
Unreviewed and stale results remain outside this denominator. Historical snapshots have their own
counts. Missing duration, usage, repair or intervention evidence stays unknown, not zero.

Paired evidence requires matching repository, goal, base commit, project profile and attachment
digest. Repeated ambiguous matches are excluded rather than cherry-picked. Matching inputs alone
does not equalize models, context, budgets or task order. The table does not declare a winner or
change an existing rollout/acceptance gate.

The ordinary desktop Codex chat has no authoritative measurement feed here and remains unmeasured.
The separate **Codex SDK · один проход** cohort contains locally collected subscription Runtime
results with verified evidence. It does not measure desktop conversations.

## Collecting a direct SDK baseline

`scripts/run_direct_baseline.py` runs one implementation invocation through the production
subscription SDK Runtime, followed by the existing deterministic quality, security and review
checks. It uses a fixed configured execution profile (`balanced` by default). There is no queue,
automatic workflow repair, independent model reviewer, commit or publication step. **One Runtime
invocation can include additional SDK turns to repair structured output**; “one pass” does not
promise exactly one model turn. A failed result stops for inspection instead of being repaired
automatically by the collector.

Prepare a separate disposable repository containing only a trusted task fixture and trusted check
commands. Initialize its project identity with `agent init`, review and commit the setup, then use
a dedicated non-default `baseline/*` branch. Record the intended full base commit SHA. The collector
requires an initialized, locally trusted target with no Git remotes, a clean checkout at exactly
that SHA, `--confirm-disposable`, and one to five explicit repository-relative file paths. Use a
new run ID for each attempt. Do not point this command at an active working repository.

`--timeout-seconds` bounds the Runtime invocation (180 seconds by default, at most 900). Each
deterministic check stage has its own 180-second bound; this option is not an overall wall-clock
deadline for the complete collection.

These are enforced boundaries: the task must classify as narrow LOW risk; the active Harness
`.agent-policy.yaml` must permit local LOW-risk patches; protected paths from the active policy
and repository registry are checked before execution and again against the output. Local project
identity grants no additional permissions. Sensitive files, control-plane files, dependency or
execution configuration, binary changes, out-of-scope files and changes over 200 lines are rejected.
The collector also checks that Git history and the allowed output boundary remain intact after
the deterministic checks. It does not automatically undo a rejected disposable result.

From a reviewed Harness source checkout with its Python environment, replace the repository, SHA,
run ID, goal and file names with the prepared fixture's values:

```sh
BASELINE_REPO=/absolute/path/to/disposable-baseline
BASELINE_SHA=FULL_RECORDED_COMMIT_SHA
python scripts/run_direct_baseline.py \
  --repo "$BASELINE_REPO" \
  --base-sha "$BASELINE_SHA" \
  --run-id baseline-csv-001 \
  --goal "Export empty values as empty CSV cells. Preserve quoting. Add a regression check." \
  --allow-path src/csv_export.py \
  --allow-path tests/test_csv_export.py \
  --execution-profile balanced \
  --confirm-disposable
```

Evidence stays under the active Harness home's `.agent-runs/<run-id>/`. A completed, checked result
receives `direct-baseline.json`, a receipt binding a saved result snapshot and required Runtime/check
files by hash. Changing or losing that evidence excludes the sample from the verified SDK cohort;
a label or manually supplied metrics cannot substitute for the receipt. Recorded SDK **attempts**
include unsuccessful collection runs, while SDK **runs** and measurement samples include only
verified completed results. **Excluded unverified** reports the difference. Inspect these counts
together: the SDK medians describe successful checked samples, not every attempt, and cannot alone
establish that this route outperforms Fast or Full.

The receipt retains the measured snapshot even if the disposable checkout is later archived.
Those measurements remain historical and unreviewed unless the user supplied an assessment.
Historical assessments are counted separately and never enter the current acceptance rate.

After collection, open **Статистика → Оценить SDK результат** to review the saved result and record
**Принято**, **Нужны правки** or **Отклонено**. Passing deterministic checks leaves acceptance at
`not_reviewed`; it does not create an independent model review or a human assessment.

SDK token measurements use recorded provider usage rather than a token count claimed in model
output. The counter follows the Harness token budget: input tokens minus cached input tokens,
plus output tokens; it is not a monetary cost estimate. Missing or invalid usage remains unknown. If structured-output recovery added turns whose
usage was not recorded completely, the token total also stays unknown; it is never filled with
zero or only the first turn's count. The table shows missing measurements and their sample sizes.

## Model choice

`GET /models?repository=...` returns the trusted project's runtime catalog. The dashboard offers
that catalog plus **По настройкам системы**. `agent task --model MODEL_ID` provides the same
explicit per-task preference. Both intake and execution validate availability. Checkpoint resume
preserves the choice, and verifier sessions remain independent. The model's supported reasoning
efforts constrain the role's settings. Model choice cannot change provider/authentication policy,
tool permissions, execution mode, or publication authority.

The installed runtime may return fewer models than a newer desktop client. An empty or unavailable
catalog does not fabricate model options; the role-profile default remains selectable. SDK upgrades
and changes to default profiles still require separate compatibility verification.
