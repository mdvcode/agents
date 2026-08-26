from __future__ import annotations

import json
from pathlib import Path

from ai_harness.evaluation.adaptive import (
    compare_adaptive_ab,
    evaluate_adaptive_plans,
    evaluate_paired_adaptive_runs,
)
from ai_harness.planning import RolePolicy, TaskAnalyzer, WorkflowCompiler


ROOT = Path(__file__).resolve().parents[1]


def test_fifty_case_golden_dataset_compiles_without_forbidden_skips() -> None:
    dataset = json.loads(
        (ROOT / "evals/datasets/adaptive_execution/golden_tasks_v1.json").read_text(
            encoding="utf-8"
        )
    )

    report = evaluate_adaptive_plans(
        dataset,
        analyzer=TaskAnalyzer(),
        compiler=WorkflowCompiler(RolePolicy.load(ROOT / ".agent-role-policy.yaml")),
    )

    assert report["case_count"] == 50
    assert report["status"] == "pass", report["blockers"]


def test_adaptive_ab_acceptance_requires_each_non_compensating_threshold() -> None:
    baseline = [
        {
            "scope": "small",
            "success": True,
            "model_calls": 10,
            "uncached_input_tokens": 100_000,
            "duration_seconds": 100,
            "quality_score": 1,
            "security_score": 1,
            "review_score": 1,
            "pr_success": 1,
        }
        for _ in range(10)
    ]
    adaptive = [
        {
            **item,
            "model_calls": 5,
            "uncached_input_tokens": 60_000,
            "duration_seconds": 70,
            "mandatory_security_gates_missed": 0,
            "high_risk_approval_bypasses": 0,
        }
        for item in baseline
    ]

    accepted = compare_adaptive_ab(baseline, adaptive)
    adaptive[0]["mandatory_security_gates_missed"] = 1
    rejected = compare_adaptive_ab(baseline, adaptive)

    assert accepted["status"] == "pass"
    assert accepted["adaptive_default_allowed"] is True
    assert rejected["status"] == "fail"
    assert rejected["adaptive_default_allowed"] is False
    assert "security_sensitive_misses_zero" in rejected["blockers"]


def test_paired_ab_collector_reads_authoritative_run_artifacts(tmp_path: Path) -> None:
    dataset = json.loads(
        (ROOT / "evals/datasets/adaptive_execution/golden_tasks_v1.json").read_text(encoding="utf-8")
    )
    pairs = []
    for case in dataset["cases"]:
        pair = {"case_id": case["id"]}
        for mode, calls, tokens, duration in (("full", 10, 100_000, 100), ("adaptive", 5, 60_000, 70)):
            run = tmp_path / f"{case['id']}-{mode}"
            artifacts = run / "artifacts"
            artifacts.mkdir(parents=True)
            roles = [{"role": "security-agent", "result": {"status": "completed"}}]
            workflow = {
                "run_id": run.name,
                "task_id": case["id"],
                "effective_mode": mode,
                "execution_status": "completed",
                "elapsed_seconds": duration,
                "roles": roles,
                "approval_grants": ([{"approval_id": "approved"}] if case["expected_risk"] == "high" else []),
            }
            metrics = {
                "model_calls_per_task": calls,
                "uncached_input_tokens_per_task": tokens,
                "time_to_success": duration,
                "repair_attempts_per_task": 0,
                "human_interventions_per_task": int(case["expected_risk"] == "high"),
            }
            (run / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
            (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
            for name, value in {
                "quality.json": {"overall_status": "pass"},
                "security.json": {"status": "pass", "verdict": "works"},
                "review.json": {"verdict": "works"},
                "publication.json": {"pr_created_or_updated": True},
            }.items():
                (artifacts / name).write_text(json.dumps(value), encoding="utf-8")
            pair[f"{mode}_run_dir"] = str(run)
        pairs.append(pair)

    report = evaluate_paired_adaptive_runs(dataset, {"schema_version": 1, "pairs": pairs})

    assert report["status"] == "pass"
    assert report["evidence_kind"] == "paired_authoritative_runs"
    assert report["dataset_cases"] == 50
    assert len(report["pairs"]) == 50
