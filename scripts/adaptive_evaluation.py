#!/usr/bin/env python3
"""Run golden-plan evaluation and authoritative paired full/adaptive acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.evaluation.adaptive import (
    acceptance_fingerprint,
    evaluate_adaptive_plans,
    evaluate_paired_adaptive_runs,
)
from ai_harness.planning import RolePolicy, TaskAnalyzer, WorkflowCompiler


def read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def write_object(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plans", "ab", "leaderboard", "gate"))
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/datasets/adaptive_execution/golden_tasks_v1.json")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, default=ROOT / "evals/adaptive_execution_acceptance.json")
    parser.add_argument("--reports", type=Path, nargs="*")
    args = parser.parse_args()

    dataset = read_object(args.dataset)
    if args.command == "plans":
        report = evaluate_adaptive_plans(
            dataset,
            analyzer=TaskAnalyzer(),
            compiler=WorkflowCompiler(RolePolicy.load(ROOT / ".agent-role-policy.yaml")),
        )
        write_object(args.report, report)
        return 0 if report["status"] == "pass" else 1
    if args.command == "ab":
        if args.manifest is None:
            parser.error("--manifest is required for ab")
        report = evaluate_paired_adaptive_runs(dataset, read_object(args.manifest))
        write_object(args.report, report)
        return 0 if report["status"] == "pass" else 1
    if args.command == "leaderboard":
        report_paths = args.reports or []
        if not report_paths:
            parser.error("--reports is required for leaderboard")
        rows = []
        for path in report_paths:
            value = read_object(path)
            metrics = value.get("metrics", {})
            metrics = metrics if isinstance(metrics, dict) else {}
            rows.append(
                {
                    "report": str(path.resolve()),
                    "status": value.get("status", ""),
                    "adaptive_success_rate": float(metrics.get("adaptive_success_rate", 0) or 0),
                    "model_call_reduction": float(metrics.get("model_call_reduction", 0) or 0),
                    "uncached_input_token_reduction": float(metrics.get("uncached_input_token_reduction", 0) or 0),
                    "median_duration_reduction": float(metrics.get("median_duration_reduction", 0) or 0),
                }
            )
        rows.sort(
            key=lambda row: (
                row["status"] == "pass",
                row["adaptive_success_rate"],
                row["model_call_reduction"],
            ),
            reverse=True,
        )
        write_object(args.report, {"schema_version": 1, "kind": "adaptive_leaderboard", "rows": rows})
        return 0

    report = read_object(args.report)
    try:
        args.report.resolve().relative_to((ROOT / ".agent-runs").resolve())
        report_is_run_scoped = True
    except ValueError:
        report_is_run_scoped = False
    allowed = (
        report_is_run_scoped
        and
        report.get("status") == "pass"
        and report.get("adaptive_default_allowed") is True
        and report.get("evidence_kind") == "paired_authoritative_runs"
        and int(report.get("dataset_cases", 0) or 0) >= 50
    )
    decision: dict[str, object] = {
        "schema_version": 1,
        "status": "pass" if allowed else "fail",
        "adaptive_default_allowed": allowed,
        "evidence_kind": report.get("evidence_kind", ""),
        "dataset_cases": int(report.get("dataset_cases", 0) or 0),
        "report_path": str(args.report.resolve()),
        "report_fingerprint": acceptance_fingerprint(args.report),
        "dataset_fingerprint": acceptance_fingerprint(args.dataset),
        "role_policy_fingerprint": acceptance_fingerprint(ROOT / ".agent-role-policy.yaml"),
        "compiler_version": "1",
        "compiler_fingerprint": acceptance_fingerprint(ROOT / "ai_harness/planning/workflow_compiler.py"),
        "checks": report.get("checks", {}),
        "blockers": (
            list(report.get("blockers", []))
            + ([] if report_is_run_scoped else ["Acceptance report must be stored under .agent-runs/."])
        ),
    }
    write_object(args.acceptance, decision)
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
