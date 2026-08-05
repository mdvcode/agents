from __future__ import annotations

from ai_harness.recovery.soak import MINIMUM_SCENARIO_COUNTS, validate_soak_manifest, validate_soak_report


def valid_manifest() -> dict[str, object]:
    tasks: list[dict[str, object]] = []
    index = 0
    for category, count in MINIMUM_SCENARIO_COUNTS.items():
        for _ in range(count):
            index += 1
            tasks.append(
                {
                    "task_key": f"soak-{index}",
                    "category": category,
                    "payload": {"task_id": f"soak-{index}", "repository": "/tmp/disposable-repository"},
                }
            )
    return {"version": 1, "tasks": tasks}


def valid_report() -> dict[str, object]:
    return {
        "version": 1,
        "duration_seconds": 7200,
        "task_count": 30,
        "scenario_counts": dict(MINIMUM_SCENARIO_COUNTS),
        "timed_out": False,
        "invariants": {
            "worker_service_survived": True,
            "recoverable_tasks_recovered": True,
            "unrecoverable_task_dead_lettered": True,
            "publication_probe_complete": True,
            "run_identity_preserved": True,
            "no_hanging_leases": True,
            "duplicate_commit_count": 0,
            "duplicate_pr_count": 0,
            "lost_run_count": 0,
            "hanging_lease_count": 0,
            "publication_runs": 1,
        },
    }


def test_soak_manifest_requires_the_exact_minimum_scenario_mix() -> None:
    manifest = valid_manifest()

    assert validate_soak_manifest(manifest) == []
    manifest["tasks"] = list(manifest["tasks"])[1:]  # type: ignore[arg-type]
    errors = validate_soak_manifest(manifest)
    assert "soak manifest must contain at least 30 tasks" in errors
    assert any("successful" in error for error in errors)


def test_soak_report_cannot_pass_without_duration_and_all_invariants() -> None:
    report = valid_report()

    assert validate_soak_report(report) == []
    report["duration_seconds"] = 60
    report["invariants"]["worker_service_survived"] = False  # type: ignore[index]
    errors = validate_soak_report(report)
    assert any("duration" in error for error in errors)
    assert "invariant worker_service_survived must be True" in errors
