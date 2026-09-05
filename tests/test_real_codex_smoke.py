from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from runtimes import create_runtime
from ai_harness.context.payload import read_snapshot


@pytest.mark.skipif(
    os.environ.get("AGENT_REAL_CODEX_SMOKE") != "1",
    reason="optional real Codex SDK smoke requires AGENT_REAL_CODEX_SMOKE=1",
)
def test_real_codex_runtime_smoke(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "README.md").write_text("smoke\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    manifest = tmp_path / "context.json"
    raw_dir = tmp_path / "raw"
    artifacts_dir = tmp_path / "artifacts"
    manifest.write_text(
        json.dumps(
            {
                "run_id": "real-smoke",
                "role": "planner",
                "goal": "Return a valid completed planner role_result JSON object for smoke testing. Create plan.md only; project_profile.json will be created deterministically by the harness.",
                "repository": str(repo),
                "artifacts_dir": str(artifacts_dir),
                "project": "agent_workspace",
                "project_profile": "agent_workspace",
                "token_budget": 1000,
                "allowed_tools": ["filesystem_read"],
                "filesystem_access": "read_only",
                "prompt_path": ".agents/prompts/planner.md",
                "output_contract": "schemas/roles/planner.schema.json",
                "expected_artifacts": ["plan.md", "project_profile.json"],
                "created_at": "2026-06-25T00:00:00+00:00",
                "context_budget": {"max_total_bytes": 120000, "max_file_bytes": 24000},
                "selected_context": [],
                "excluded_context": [],
                "retrieval_queries": [],
                "source_file_candidates": [],
                "repo_intelligence": {},
                "context_files": [],
                "artifact_references": [],
                "skill_references": [],
                "previous_roles": [],
                "retrieval_rules": [],
                "raw_outputs_dir": str(raw_dir),
            }
        ),
        encoding="utf-8",
    )
    request = {
        "run_id": "real-smoke",
        "role": "planner",
        "goal": "Return a valid completed planner role_result JSON object for smoke testing. Create plan.md only; project_profile.json will be created deterministically by the harness.",
        "repository": str(repo),
        "artifacts_dir": str(artifacts_dir),
        "context_manifest": str(manifest),
        "prompt_path": ".agents/prompts/planner.md",
        "output_contract": "schemas/roles/planner.schema.json",
        "project_profile": "agent_workspace",
        "expected_artifacts": ["plan.md", "project_profile.json"],
        "allowed_tools": ["filesystem_read"],
        "filesystem_access": "read_only",
        "token_budget": 1000,
        "timeout_seconds": 60,
    }

    runtime = create_runtime(
        raw_output_dir=raw_dir,
        timeout_seconds=90,
    )
    result = runtime.execute(
        role="planner",
        context=manifest,
        task=request,
        worktree=repo,
        artifacts=artifacts_dir,
    )

    assert result["status"] == "completed", result
    assert runtime.descriptor.provider == "codex-sdk"
    assert runtime.descriptor.transport == "local_subscription"
    assert runtime.descriptor.api_required is False
    assert (artifacts_dir / "plan.md").exists()
    assert (artifacts_dir / "project_profile.json").exists()
    assert result["thread_id"]
    assert isinstance(result["input_tokens"], int)
    assert isinstance(result["output_tokens"], int)
    assert isinstance(result["duration_ms"], int)
    assert (raw_dir / "planner.jsonl").exists()
    snapshots = list((tmp_path / "context-manifests/effective").glob("*.json"))
    assert snapshots, "real SDK turn must retain its exact input snapshot"
    effective = read_snapshot(snapshots[0])
    assert effective["payload"]["runtime"] == "codex-sdk"
    assert effective["payload"]["thread_id"] == result["thread_id"]
    assert "Human-interaction policy:" in effective["payload"]["prompt"]
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=False)
    assert status.stdout == ""
