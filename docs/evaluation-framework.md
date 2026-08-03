# Evaluation Framework

Milestone 3 begins with an offline, deterministic evaluation plane for authoritative Harness runs. It can show whether a model, prompt, retrieval, loop, or memory variant improved the measured engineering outcome without giving missing telemetry a free pass.

## Design

The framework separates five concerns:

1. A dataset defines stable cases and expectations using symbolic subject names.
2. A rubric defines one quality dimension per metric, its evidence source, weight, required status, and normalization.
3. `score.py` reads one completed `.agent-runs/<run-id>` and emits a scorecard.
4. `run_evals.py` applies dataset expectations; `compare_runs.py` accepts only paired reports with matching dataset and rubric fingerprints.
5. `leaderboard.py` ranks only entries that meet the configured evidence-coverage floor.

Evaluation is read-only with respect to the subject run. Inputs cannot provide shell commands, and hidden expectations stay in the evaluator plane rather than the evaluated run context.

## Metrics

The first rubric covers planning, code quality, security, review quality, PR success, repair success, context quality, latency, tokens, cost, and human interventions. Every metric has one normalized `0..1` score or an explicit `unavailable` / `not_applicable` state.

The composite score is normalized over available weighted metrics. `coverage` is the fraction of total rubric weight actually scored. Required missing metrics or coverage below the rubric floor produce `insufficient_evidence`, even if the available-metric score is high.

Cost is reported only when `metrics.json` contains `cost_usd` or the selected rubric supplies explicit model pricing. The default rubric never invents a price.

## Commands

Score one run:

```sh
python3 scripts/score.py \
  --run-dir .agent-runs/<run-id> \
  --rubric evals/rubrics/harness_run_v1.json \
  --output .agent-runs/<evidence-run>/artifacts/evals/<run-id>.json
```

Run the starter clean-control dataset:

```sh
python3 scripts/run_evals.py \
  --dataset evals/datasets/harness_completed_run_v1.json \
  --subject candidate=.agent-runs/<run-id> \
  --variant candidate=variant.json \
  --output .agent-runs/<evidence-run>/artifacts/evals/candidate.json
```

`variant.json` is optional experiment metadata such as `model`, `prompt`, `retriever`, `loop`, and `memory`. It is recorded in the scorecard but does not change the frozen dataset or rubric fingerprint.

Compare frozen baseline and candidate reports:

```sh
python3 scripts/compare_runs.py \
  --baseline baseline.json \
  --candidate candidate.json \
  --output comparison.json
```

Build a leaderboard:

```sh
python3 scripts/leaderboard.py baseline.json candidate.json \
  --output leaderboard.json \
  --markdown leaderboard.md
```

## Adding a benchmark

- Start with one capability and one evaluator dimension at a time.
- Include a realistic failing case and a trustworthy clean control together.
- Freeze task inputs and environment state before comparing variants.
- Prefer deterministic evidence for objective outcomes; introduce a calibrated semantic judge only when code cannot decide the criterion.
- Run both the subject and verifier, inspect false positives/negatives, and revise the verifier before treating it as a gate.
- Promote production failures into `evals/regressions/` only after their expected category and evidence are reviewed.

## Research basis

- [LangChain Eval Engineering skill](https://github.com/langchain-ai/langchain-skills) separates harness, environment, task, and independent verifier and insists on auditing valid and invalid results.
- [Towards Automating Eval Engineering](https://www.langchain.com/blog/towards-automating-eval-engineering) emphasizes repository/trace-informed task creation, reproducible environments, stable evals across agent variants, and iterative verifier review.
- [IssueBench](https://www.langchain.com/blog/issuebench-how-we-evaluate-engine) demonstrates frozen taxonomies, synthetic but realistic ground truth, clean/no-issue controls, cross-domain cases, and end-to-end scoring of the useful artifact.

Harbor task execution and LLM-as-judge are deliberate later adapters. The current milestone creates the local contracts and deterministic baseline without adding Docker, credentials, vendor APIs, or a second runtime path.
