# Evaluation Framework

## Adaptive execution v1

`datasets/adaptive_execution/golden_tasks_v1.json` is the 50-case deterministic role-selection corpus. `ai_harness.evaluation.adaptive.evaluate_adaptive_plans` verifies risk/scope classification, required roles, and forbidden skips. `compare_adaptive_ab` evaluates paired full/adaptive evidence and keeps adaptive mode opt-in unless every efficiency, quality, security, and approval threshold passes.

Run the deterministic corpus with `make adaptive-eval-plans ADAPTIVE_REPORT=<run-artifact-path>`. For acceptance, prepare a manifest with one `case_id`, `full_run_dir`, and `adaptive_run_dir` entry for every golden case, then run `make adaptive-eval-ab ADAPTIVE_MANIFEST=<manifest> ADAPTIVE_REPORT=<report>`. Generate comparisons with `make adaptive-eval-leaderboard EVAL_REPORTS="<report...>"`; only after a passing authoritative report may `make adaptive-eval-gate ADAPTIVE_REPORT=<report>` enable adaptive-by-default. The decision is invalidated automatically when role policy or compiler version changes.

This directory contains versioned evaluation inputs. Generated reports do not belong here; write them to the owning `.agent-runs/<run-id>/artifacts/evals/` directory or another explicitly chosen evidence directory.

## Layout

- `datasets/`: portable cases that map symbolic subjects to Harness run directories at invocation time.
- `benchmarks/`: frozen benchmark composition and comparison gates.
- `golden_tasks/`: reviewed capability and expected-evidence definitions.
- `regressions/`: known failure taxonomy and clean control cases.
- `rubrics/`: one-dimensional metric definitions, weights, required evidence, and normalization thresholds.
- `baselines/`: compact frozen production-corpus baselines.
- `experiments/`: dataset composition, candidate generator metadata, and regression thresholds.

The evaluated run never reads dataset expectations. Evaluation scripts only read completed run artifacts and never execute commands found in inputs.

## Quick start

```sh
python3 scripts/score.py \
  --run-dir .agent-runs/<run-id> \
  --output .agent-runs/<evidence-run>/artifacts/evals/<run-id>.json

python3 scripts/run_evals.py \
  --dataset evals/datasets/harness_completed_run_v1.json \
  --subject candidate=.agent-runs/<run-id> \
  --variant candidate=variant.json \
  --output .agent-runs/<evidence-run>/artifacts/evals/candidate.json
```

Run the same frozen dataset and rubric for a baseline and candidate, then use `compare_runs.py`. Only reports with matching dataset and rubric fingerprints are comparable.

Run the production corpus and CI-compatible regression gate with `make eval-regression`. Version-2 corpus cases are declarative snapshots and cannot contain executable command fields. Their fingerprints exclude observed candidate evidence but include every frozen task and expectation; a separate scorer-contract fingerprint protects rubric compatibility.
