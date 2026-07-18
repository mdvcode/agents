from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_step1_series.py"
SPEC = importlib.util.spec_from_file_location("verify_step1_series", MODULE_PATH)
assert SPEC is not None
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_run(runs: Path, index: int, risk_class: str) -> Path:
    run = runs / f"run-{index}"
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    low_medium_roles = sorted(verifier.REQUIRED_LOW_MEDIUM_ROLES)
    high_roles = sorted(verifier.REQUIRED_HIGH_ROLES)
    roles = high_roles if risk_class == "high" else low_medium_roles
    write_json(
        run / "workflow.json",
        {
            "run_id": run.name,
            "execution_status": "awaiting_approval" if risk_class == "high" else "completed",
            "roles": [
                {
                    "role": role,
                    "result": {
                        "status": "awaiting_approval" if role == "approval-gate" else "completed"
                    },
                }
                for role in roles
            ],
            "executor": {
                "kind": "codex_cli",
                "production": True,
                "command": "python3 scripts/adapters/codex_cli_executor.py",
            },
            "input_fingerprint": f"fingerprint-{index}",
            "base_branch_sha_before": "abc",
            "base_branch_sha_after": "abc",
        },
    )
    write_json(artifacts / "risk.json", {"risk_class": risk_class})
    write_json(run / "metrics.json", {"tokens_used": 5})
    (run / "raw-events").mkdir()
    (run / "raw-events" / "planner.jsonl").write_text(
        '{"type":"thread.started","thread_id":"thread"}\n', encoding="utf-8"
    )
    if risk_class == "high":
        (run / "errors.jsonl").write_text(
            json.dumps({"stage": "routing", "code": "AWAITING_APPROVAL", "message": "high"}) + "\n",
            encoding="utf-8",
        )
    else:
        write_json(
            artifacts / "publication.json",
            {
                "execution_status": "completed",
                "pr_created_or_updated": True,
                "pr_url": f"https://example.test/pr/{index}",
                "commit_sha": f"sha-{index}",
            },
        )
    return run


def test_ten_real_executor_records_close_series_gate(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    for index in range(10):
        make_run(runs, index, "high" if index in {8, 9} else ("low" if index % 2 else "medium"))
    result = verifier.verify_series(runs)
    assert result["status"] == "pass"
    assert result["task_count"] == 10
    assert result["real_executor_runs"] == 10
    assert result["high_stopped"] == 2


def test_series_rejects_duplicate_prs_for_same_input(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    for index in range(10):
        run = make_run(runs, index, "high" if index == 9 else "medium")
        if index in {0, 1}:
            state = json.loads((run / "workflow.json").read_text(encoding="utf-8"))
            state["input_fingerprint"] = "duplicate"
            write_json(run / "workflow.json", state)
    result = verifier.verify_series(runs)
    assert result["status"] == "fail"
    assert result["duplicate_publications"] == ["duplicate"]


def test_series_minimum_cannot_be_weakened_below_ten(tmp_path: Path) -> None:
    runs = tmp_path / ".agent-runs"
    make_run(runs, 0, "medium")
    make_run(runs, 1, "high")

    result = verifier.verify_series(runs, minimum_tasks=2)

    assert result["status"] == "fail"
    assert "Step 1 minimum_tasks cannot be lower than 10" in result["blockers"]
