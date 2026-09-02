from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.run_state import RunLayout, write_metrics


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_efficiency_metrics_use_executed_checks_approval_events_and_pr_time(tmp_path: Path) -> None:
    layout = RunLayout.create(tmp_path, "run")
    started = datetime.now(timezone.utc)
    write(layout.artifacts / "quality.json", {"checks": [{"status": "pass"}, {"status": "pass"}]})
    write(layout.artifacts / "security.json", {"evidence": [{"status": "pass"}]})
    write(
        layout.artifacts / "publication.json",
        {
            "pr_created_or_updated": True,
            "pr_published_at": (started + timedelta(seconds=42)).isoformat(),
        },
    )
    layout.raw_events.joinpath("approvals.jsonl").write_text(
        json.dumps({"event": "approval.requested"}) + "\n",
        encoding="utf-8",
    )
    state = {
        "execution_status": "completed",
        "started_at": started.isoformat(),
        "elapsed_seconds": 50,
        "roles": [
            {
                "role": "implementation-agent",
                "llm_invoked": True,
                "result": {"status": "completed", "input_tokens": 100, "cached_input_tokens": 25, "output_tokens": 20},
            }
        ],
        "loops": {},
    }

    write_metrics(layout, state)
    metrics = json.loads(layout.metrics.read_text(encoding="utf-8"))

    assert metrics["deterministic_checks_per_task"] == 3
    assert metrics["human_interventions_per_task"] == 1
    assert metrics["output_tokens_per_task"] == 20
    assert metrics["time_to_accepted_pr"] == 42
    assert metrics["successful_task_token_cost"] == 95


def test_metrics_share_terminal_and_replay_accounting_with_workflow(tmp_path: Path) -> None:
    layout = RunLayout.create(tmp_path, "accounted-run")
    actual_result = {
        "status": "completed",
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 10,
        "tokens_used": 90,
    }
    state = {
        "execution_status": "running",
        "roles": [
            {
                "role": "implementation-agent",
                "llm_invoked": True,
                "execution_profile": {"escalation_level": 1},
                "result": actual_result,
            },
            {
                "role": "implementation-agent",
                "llm_invoked": True,
                "execution_profile": {
                    "escalation_level": 2,
                    "terminal_action": "human_or_dead_letter",
                },
                "result": {"status": "awaiting_approval", "tokens_used": 0},
            },
            {
                "role": "implementation-agent",
                "llm_invoked": False,
                "cache_provenance": "completed_checkpoint_replay",
                "result": actual_result,
            },
            {
                "role": "approval-gate",
                "llm_invoked": False,
                "result": {"status": "awaiting_approval"},
            },
        ],
        "loops": {},
    }

    write_metrics(layout, state)
    metrics = json.loads(layout.metrics.read_text(encoding="utf-8"))

    assert metrics["role_count"] == 1
    assert metrics["roles_executed_per_task"] == 1
    assert metrics["tokens_used"] == 90
    assert metrics["model_calls_per_task"] == 1
    assert metrics["model_escalations_per_task"] == 1
