from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_harness.evaluation.comparison import compare_reports
from ai_harness.evaluation.io import EvaluationInputError
from ai_harness.evaluation.leaderboard import build_leaderboard
from ai_harness.evaluation.runner import evaluate_dataset
from ai_harness.evaluation.scoring import METRIC_NAMES, score_run


ROOT = Path(__file__).resolve().parents[1]
RUBRIC = json.loads((ROOT / "evals" / "rubrics" / "harness_run_v1.json").read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_run(root: Path, name: str = "run", *, quality_status: str = "pass") -> Path:
    run = root / name
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    write_json(
        run / "workflow.json",
        {
            "run_id": name,
            "project_profile": "agent_workspace",
            "execution_status": "completed",
            "loops": {
                "quality_repair": {"iterations": 1},
                "review_repair": {"iterations": 0},
            },
            "runtime": {"provider": "codex-cli", "model": "candidate-model"},
            "eval_variant": {"prompt": "v2", "retriever": "rules-v1"},
        },
    )
    (artifacts / "plan.md").write_text(
        "# Goal\n\n## GOAL\nDo it.\n\n## CONTEXT\nRepo.\n\n## PLAN\n1. Work.\n\n"
        "## DONE WHEN\nDone.\n\n## VERIFY\nRun tests.\n",
        encoding="utf-8",
    )
    write_json(
        artifacts / "quality.json",
        {
            "overall_status": quality_status,
            "commands_attempted": [
                {"command": "focused", "status": quality_status},
                {"command": "full", "status": quality_status},
            ],
        },
    )
    write_json(artifacts / "security.json", {"status": "pass", "highest_severity": "none"})
    write_json(artifacts / "review.json", {"status": "pass", "verdict": "works", "findings": []})
    write_json(
        artifacts / "publication.json",
        {
            "execution_status": "completed",
            "commit_created": True,
            "branch_pushed": True,
            "pr_created_or_updated": True,
            "pr_url": "https://example.test/pr/1",
        },
    )
    write_json(
        run / "metrics.json",
        {
            "duration_ms": 250000,
            "tokens_used": 40000,
            "roles": [
                {
                    "input_tokens": 30000,
                    "cached_input_tokens": 10000,
                    "output_tokens": 10000,
                }
            ],
        },
    )
    log = run / "context-manifests" / "logs" / "planner.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {
                "budget": {"total_tokens": 12000, "used_tokens": 4000, "remaining_tokens": 8000},
                "selected": [{"path": "AGENTS.md"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run


def metric_map(scorecard: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(item["name"]): item for item in scorecard["metrics"]}  # type: ignore[index]


def test_score_run_covers_requested_metrics_and_never_invents_cost(tmp_path: Path) -> None:
    scorecard = score_run(make_run(tmp_path), RUBRIC)
    metrics = metric_map(scorecard)

    assert tuple(metrics) == METRIC_NAMES
    assert scorecard["status"] == "pass"
    assert scorecard["coverage"] == pytest.approx(0.96)
    assert metrics["planning"]["score"] == 1.0
    assert metrics["repair_success"]["score"] == 1.0
    assert metrics["context_quality"]["score"] == 1.0
    assert metrics["cost"]["status"] == "unavailable"
    assert metrics["cost"]["observed"] == {"usd": None, "source": "unavailable"}


def test_missing_required_artifact_is_insufficient_evidence(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    (run / "artifacts" / "security.json").unlink()

    scorecard = score_run(run, RUBRIC)

    assert scorecard["status"] == "insufficient_evidence"
    assert scorecard["required_unavailable"] == ["security"]
    assert "required metrics unavailable: security" in scorecard["blockers"]


def test_dataset_expectations_gate_clean_control(tmp_path: Path) -> None:
    dataset = json.loads(
        (ROOT / "evals" / "datasets" / "harness_completed_run_v1.json").read_text(encoding="utf-8")
    )

    result = evaluate_dataset(
        dataset,
        RUBRIC,
        {"candidate": make_run(tmp_path)},
        {"candidate": {"model": "test-model", "memory": "disabled"}},
    )

    assert result["status"] == "pass"
    assert result["passed_cases"] == 1
    assert result["cases"][0]["scorecard"]["subject"]["variant"]["model"] == "test-model"


def test_comparison_detects_metric_regression_and_rejects_incompatible_rubric(tmp_path: Path) -> None:
    baseline = score_run(make_run(tmp_path, "baseline"), RUBRIC)
    candidate = score_run(make_run(tmp_path, "candidate", quality_status="fail"), RUBRIC)

    comparison = compare_reports(baseline, candidate)

    assert comparison["status"] == "regression"
    assert any("code_quality regressed" in blocker for blocker in comparison["blockers"])

    candidate["rubric"]["fingerprint"] = "different"  # type: ignore[index]
    with pytest.raises(EvaluationInputError, match="different rubric"):
        compare_reports(baseline, candidate)


def test_comparison_blocks_lost_metric_evidence(tmp_path: Path) -> None:
    baseline = score_run(make_run(tmp_path, "baseline"), RUBRIC)
    candidate_run = make_run(tmp_path, "candidate")
    (candidate_run / "context-manifests" / "logs" / "planner.jsonl").unlink()
    candidate = score_run(candidate_run, RUBRIC)

    comparison = compare_reports(baseline, candidate)

    assert comparison["status"] == "regression"
    assert "single: candidate lost scored metric context_quality" in comparison["blockers"]


def test_leaderboard_ranks_only_entries_with_enough_coverage(tmp_path: Path) -> None:
    eligible = score_run(make_run(tmp_path, "eligible"), RUBRIC)
    incomplete_run = make_run(tmp_path, "incomplete")
    for name in ("quality.json", "security.json", "review.json", "publication.json"):
        (incomplete_run / "artifacts" / name).unlink()
    incomplete = score_run(incomplete_run, RUBRIC)

    leaderboard = build_leaderboard([("eligible.json", eligible), ("incomplete.json", incomplete)])

    assert leaderboard["eligible_count"] == 1
    assert leaderboard["entries"][0]["label"] == "eligible"
    assert leaderboard["entries"][0]["rank"] == 1
    assert leaderboard["entries"][1]["rank"] is None


def test_score_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    output = tmp_path / "scorecard.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/score.py",
            "--run-dir",
            str(make_run(tmp_path)),
            "--rubric",
            "evals/rubrics/harness_run_v1.json",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["kind"] == "scorecard"
