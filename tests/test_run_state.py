from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_state.py"
SPEC = importlib.util.spec_from_file_location("run_state_test", MODULE_PATH)
assert SPEC is not None
run_state = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = run_state
SPEC.loader.exec_module(run_state)


def test_layout_contains_every_authoritative_state_area(tmp_path: Path) -> None:
    layout = run_state.RunLayout.create(tmp_path / ".agent-runs", "run-1")
    assert layout.workflow == layout.root / "workflow.json"
    assert layout.context.name == "context-manifests"
    assert layout.role_results.name == "role-results"
    assert layout.raw_events.name == "raw-events"
    assert layout.artifacts.name == "artifacts"
    assert layout.metrics == layout.root / "metrics.json"
    assert layout.errors == layout.root / "errors.jsonl"
    assert not (tmp_path / "artifacts").exists()


def test_layout_rejects_parallel_artifacts_directory(tmp_path: Path) -> None:
    layout = run_state.RunLayout.create(tmp_path / ".agent-runs", "run-1")
    try:
        layout.assert_artifacts_dir(tmp_path / "artifacts")
    except ValueError as exc:
        assert "mutable artifact mirrors are forbidden" in str(exc)
    else:
        raise AssertionError("parallel artifacts directory was accepted")


def test_artifact_ownership_detects_foreign_write(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    before = run_state.file_snapshot(artifacts)
    (artifacts / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (artifacts / "verdict.json").write_text("{}\n", encoding="utf-8")
    errors = run_state.ownership_errors(
        role="planner",
        allowed_artifacts=["plan.md"],
        before=before,
        after=run_state.file_snapshot(artifacts),
    )
    assert errors == ["planner modified artifact owned by another role: verdict.json"]


def test_artifact_ownership_allows_owned_evidence_namespace(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    evidence = artifacts / "frontend-evidence"
    evidence.mkdir(parents=True)
    before = run_state.file_snapshot(artifacts)
    (evidence / "screen.png").write_bytes(b"png")

    errors = run_state.ownership_errors(
        role="frontend-qa-agent",
        allowed_artifacts=["frontend_qa.json", "frontend-evidence/**"],
        before=before,
        after=run_state.file_snapshot(artifacts),
    )

    assert errors == []


def test_metrics_persist_role_token_usage(tmp_path: Path) -> None:
    layout = run_state.RunLayout.create(tmp_path / ".agent-runs", "run-1")
    run_state.write_metrics(
        layout,
        {
            "execution_status": "completed",
            "roles": [
                {
                    "role": "planner",
                    "execution_profile": {
                        "execution_profile": "balanced",
                        "model": "gpt-5.6-terra",
                        "reasoning_effort": "medium",
                        "escalation_level": 0,
                    },
                    "result": {
                        "status": "completed",
                        "tokens_used": 12,
                        "input_tokens": 8,
                        "output_tokens": 4,
                        "duration_ms": 25,
                    },
                }
            ],
        },
    )
    metrics = json.loads(layout.metrics.read_text(encoding="utf-8"))
    assert metrics["tokens_used"] == 12
    assert metrics["roles"][0]["input_tokens"] == 8
    assert metrics["roles"][0]["execution_profile"] == "balanced"
    assert metrics["roles"][0]["model"] == "gpt-5.6-terra"


def test_completed_input_is_deduplicated(tmp_path: Path) -> None:
    layout = run_state.RunLayout.create(tmp_path / ".agent-runs", "run-1")
    run_state.write_json(
        layout.workflow,
        {
            "run_id": "run-1",
            "input_fingerprint": "same",
            "execution_status": "completed",
        },
    )
    assert run_state.find_completed_run(tmp_path / ".agent-runs", "same") == {
        "run_id": "run-1",
        "input_fingerprint": "same",
        "execution_status": "completed",
    }


def test_empty_attachment_digest_preserves_legacy_task_fingerprint(tmp_path: Path) -> None:
    values = {
        "task_id": "task-1",
        "goal": "Keep legacy identity",
        "repository": tmp_path,
        "branch": "codex/task-1",
        "base_branch": "main",
        "workspace_mode": "worktree",
        "workflow_mode": "auto",
    }
    legacy_payload = {
        **values,
        "repository": str(tmp_path.resolve()),
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()

    assert run_state.task_fingerprint(**values) == expected
    assert run_state.task_fingerprint(
        **values,
        input_manifest_sha256="a" * 64,
    ) != expected
