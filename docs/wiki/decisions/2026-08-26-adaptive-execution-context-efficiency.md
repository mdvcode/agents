# Adaptive Execution And Context Efficiency v1

Date: 2026-08-26

## Status

Implemented behind explicit `adaptive` mode. Automatic/default activation remains gated by paired evaluation acceptance.

## Decision

The Harness compiles one immutable, auditable execution plan before model-backed roles run:

```text
task envelope
  -> deterministic TaskAnalyzer
  -> declarative role policy
  -> immutable ExecutionPlan DAG
  -> existing checkpointed role runner
  -> existing approval/publication boundaries
```

The new layer is an optimization boundary, not a second production runtime. `ROLE_CHAIN` remains the full fallback, `fast` retains its guarded policy, and `goal` remains explicit. Recovery, queue leases, worktree identity, security severity routing, scoped approvals, and publication idempotency continue to use their existing authorities.

## Task analysis and role policy

`TaskAnalyzer` classifies task class, scope, risk hints, domains, requested paths, deterministic tool availability, and historical failure signals without an LLM. Low confidence compiles a safe full-equivalent plan.

`.agent-role-policy.yaml` declares role conditions, hard gates, read/write behavior, role context ceilings, task budgets, and profile-specific deterministic checks. The compiler may skip optional work, but it must include issue intake, context compilation, implementation, quality, security, review, orchestration, and publication preparation. Existing HIGH-risk and security approval rules remain authoritative even if a compiled plan is malformed.

## DAG and parallelism

The plan records nodes, dependency edges, parallel groups, required/skipped roles, model profiles, deterministic checks, budgets, and role-selection reasons. The run stores it at `.agent-runs/<run-id>/execution-plan.json` and saves its fingerprint in `workflow.json`; resume fails closed if the file changed.

Only independent read-only nodes with the same dependency frontier may share a parallel group. The executor runs the complete ready verification frontier concurrently after the final planned write role, including model-backed security, frontend, architecture, and semantic verification when selected. Each model-backed verifier performs its deterministic preflight first and writes only its owned artifact. Write roles are never parallelized in one worktree.

## Context efficiency

Context Engine uses a content-addressed local cache under ignored `.agent-cache/context/`. The key includes repository HEAD, a content-sensitive dirty fingerprint, role/query fingerprint, project-profile version, policy version, and context compiler version.

Exact input matches avoid source discovery and compilation. The query fingerprint also covers the current run-artifact snapshot and runtime delta, so role outputs cannot produce a stale exact hit. When only unrelated repository state changes, compatible reuse is allowed only if every previously selected source fingerprint still matches; stale compatible entries are selectively evicted.

Before retrieval, exact and conservative near-duplicate documents are removed with authoritative, newest, and explicit `supersedes` relationships preferred. Role context contains only relevant task artifacts. The delta layer records changed artifacts, new failures, and new routing/budget decisions since the preceding role manifest. Role token budgets are ceilings, not fill targets.

## Models and task budgets

Model selection remains deterministic inside the configured production runtime. Work starts with the cheapest sufficient profile. Deterministic failures such as pytest/lint/type failures stay in repair loops. Reasoning failures first increase reasoning effort on the current model and only then advance the bounded `economy -> balanced -> complex` model ladder; exhausting complex/xhigh requires human review or dead-letter. Capability requirements, context size, repair history, budget pressure, and accepted eval history are explicit inputs. Provider selection and Model Router remain unchanged.

The task budget controller tracks model calls, uncached input, output, elapsed time, repairs, and escalations. Model-call and token ceilings are soft cost signals: exceeding them selects economy execution for mandatory work and sheds optional roles without requesting approval. Elapsed-time, repair-attempt, and escalation ceilings remain hard bounds that may require approval. A consumed hard-bound approval records a new bounded baseline only for the exhausted hard dimensions; it does not reset cumulative telemetry, skip another authority gate, or rerun a completed role checkpoint. Mandatory verification and security gates are never skipped.

## Evaluation gate

The adaptive corpus contains 50 deterministic golden tasks across trivial/docs, small bugfixes, tests/refactors, medium features, security-sensitive work, and architecture/high-risk work. Paired A/B acceptance is non-compensating:

- model calls decrease by at least 40% for trivial/small/medium tasks;
- uncached input decreases by at least 30%;
- median duration decreases by at least 25%;
- success rate regresses by no more than 2 percentage points;
- mandatory security misses and HIGH-risk approval bypasses remain zero.

`scripts/adaptive_evaluation.py` reads paired authoritative run directories, produces the A/B report and leaderboard, and writes the fingerprint-bound default decision. `auto` selects adaptive only while that decision still matches the current role policy and compiler version. Until real paired evidence passes, `auto` preserves the previously accepted behavior and `adaptive` must be selected explicitly.

## Deferred work

Vector databases, semantic/episodic memory, GraphRAG, LangGraph, distributed workers, remote sandbox fleets, new roles, and self-modifying prompts remain out of scope.
