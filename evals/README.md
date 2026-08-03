# Evaluation Framework

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
