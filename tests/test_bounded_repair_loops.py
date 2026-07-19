from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_router import decide_next_role, failure_fingerprint  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def state(diff_hash: str) -> dict[str, object]:
    return {
        "workflow": "full_agent_workflow",
        "roles": [],
        "loops": {
            "quality_repair": {"iterations": 0},
            "review_repair": {"iterations": 0},
            "ci_repair": {"iterations": 0},
            "frontend_verification_repair": {"iterations": 0},
        },
        "diff_hash": diff_hash,
    }


def test_same_quality_failure_without_diff_progress_stops_at_approval(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    write_json(artifacts / "quality.json", {"overall_status": "fail", "failed_command": "pytest", "stderr_excerpt": "same"})
    current = state("same-diff")
    first = decide_next_role(
        current_role="quality-runner",
        role_result={"status": "completed", "next_action": "continue"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=current,
    )
    second = decide_next_role(
        current_role="quality-runner",
        role_result={"status": "completed", "next_action": "continue"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=current,
    )
    assert first["next_role"] == "implementation-agent"
    assert second["next_role"] == "approval-gate"
    assert second["stop"] is True


def test_quality_loop_is_bounded_even_when_diff_changes(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    write_json(artifacts / "quality.json", {"overall_status": "fail", "failed_command": "pytest"})
    current = state("diff-1")
    routes = []
    for index in range(3):
        current["diff_hash"] = f"diff-{index}"
        routes.append(
            decide_next_role(
                current_role="quality-runner",
                role_result={"status": "completed", "next_action": "publication"},
                run_dir=tmp_path,
                artifacts_dir=artifacts,
                workflow_state=current,
            )
        )
    assert [route["loop"]["iteration"] for route in routes] == [1, 2, 3]
    assert routes[-1]["next_role"] == "approval-gate"


def test_failure_fingerprint_is_independent_from_diff_fingerprint(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    write_json(artifacts / "quality.json", {"failed_command": "pytest", "stderr_excerpt": "failed"})
    first = failure_fingerprint(
        role_result={"blockers": ["Q1"]},
        state={"diff_hash": "one", "changed_files": ["src/a.py"]},
        artifacts_dir=artifacts,
    )
    second = failure_fingerprint(
        role_result={"blockers": ["Q1"]},
        state={"diff_hash": "two", "changed_files": ["src/a.py"]},
        artifacts_dir=artifacts,
    )
    assert first == second


def test_ci_failure_routes_through_ci_repair_agent(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    write_json(artifacts / "quality.json", {"overall_status": "fail", "ci_status": "fail", "failed_command": "ci"})
    current = state("ci-diff")
    result = decide_next_role(
        current_role="quality-runner",
        role_result={"status": "completed", "next_action": "publication"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=current,
    )
    assert result["next_role"] == "ci-repair-agent"
    assert result["loop"]["name"] == "ci_repair"


def test_distinct_repair_loops_do_not_share_a_three_iteration_ceiling(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    write_json(artifacts / "quality.json", {"overall_status": "fail", "failed_command": "pytest"})
    current = state("quality-diff")
    current["loops"] = {
        "quality_repair": {"iterations": 0},
        "review_repair": {"iterations": 1},
        "ci_repair": {"iterations": 1},
        "frontend_verification_repair": {"iterations": 1},
    }

    result = decide_next_role(
        current_role="quality-runner",
        role_result={"status": "completed"},
        run_dir=tmp_path,
        artifacts_dir=artifacts,
        workflow_state=current,
    )

    assert result["next_role"] == "implementation-agent"
    assert result["loop"]["iteration"] == 1
