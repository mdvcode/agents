from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from runtimes import create_runtime
from ai_harness.context.payload import read_snapshot
from ai_harness.model_policy import load_execution_profiles
from ai_harness.sdk_session import ManagedCodexSdkSession


@pytest.mark.skipif(
    os.environ.get("AGENT_REAL_CODEX_SMOKE") != "1",
    reason="optional real Codex SDK smoke requires AGENT_REAL_CODEX_SMOKE=1",
)
@pytest.mark.parametrize("selected_model", [False, True], ids=["default", "selected"])
def test_real_codex_runtime_smoke(tmp_path: Path, selected_model: bool) -> None:
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
    selected_entry = None
    if selected_model:
        catalog = runtime.list_models(worktree=repo, timeout_seconds=20)
        assert catalog["status"] == "available", catalog
        assert catalog["models"], "authenticated runtime must return at least one visible model"
        default_model = load_execution_profiles()["balanced"]["model"]
        selected_entry = next(
            (entry for entry in catalog["models"] if entry["id"] != default_model),
            catalog["models"][0],
        )
        request.update({"model_override": selected_entry["id"], "model": selected_entry["id"]})
    result = runtime.execute(
        role="planner",
        context=manifest,
        task=request,
        worktree=repo,
        artifacts=artifacts_dir,
    )

    assert result["status"] == "completed", result
    if selected_entry is not None:
        assert result["model"] == selected_entry["id"]
        assert result["reasoning_effort"] in selected_entry["supported_reasoning_efforts"]
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


@pytest.mark.skipif(
    os.environ.get("AGENT_REAL_CODEX_SMOKE") != "1",
    reason="optional real Codex SDK smoke requires AGENT_REAL_CODEX_SMOKE=1",
)
def test_real_sdk_managed_session_resumes_and_keeps_reviewer_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "README.md").write_text("managed SDK smoke\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    prompt = tmp_path / "smoke-prompt.md"
    prompt.write_text("Follow the goal. Return a completed role result. Do not use tools, create artifacts, or change files.\n", encoding="utf-8")
    run_id = "managed-sdk-smoke"
    marker = "orchid-" + uuid.uuid4().hex[:8]
    session = ManagedCodexSdkSession(
        worker_id="smoke-" + uuid.uuid4().hex,
        harness_root=SCRIPTS.parent, state_root=tmp_path / "sessions",
    )

    def invoke(role: str, goal: str, number: int) -> dict[str, object]:
        step = tmp_path / f"step-{number}"
        artifacts = step / "artifacts"
        artifacts.mkdir(parents=True)
        context = step / "context.json"
        request = {
            "run_id": run_id, "role": role, "goal": goal, "repository": str(repo),
            "artifacts_dir": str(artifacts), "context_manifest": str(context), "prompt_path": str(prompt),
            "output_contract": str(SCRIPTS.parent / "schemas/role_result.schema.json"),
            "project_profile": "agent_workspace", "expected_artifacts": [], "allowed_tools": [],
            "filesystem_access": "read_only", "token_budget": 1000, "timeout_seconds": 60,
        }
        manifest = {
            **request, "project": "agent_workspace", "created_at": "2026-10-08T00:00:00+00:00",
            "context_budget": {"max_total_bytes": 120000, "max_file_bytes": 24000},
            "selected_context": [], "excluded_context": [], "retrieval_queries": [], "source_file_candidates": [],
            "repo_intelligence": {}, "context_files": [], "artifact_references": [], "skill_references": [],
            "previous_roles": [], "retrieval_rules": [], "raw_outputs_dir": str(step / "raw"),
        }
        context.write_text(json.dumps(manifest), encoding="utf-8")
        runtime = create_runtime(raw_output_dir=step / "raw", timeout_seconds=60)
        result = runtime.execute(role=role, context=context, task=request, worktree=repo, artifacts=artifacts)
        assert result["status"] == "completed", result
        assert result["thread_id"]
        assert (step / "raw" / f"{role}.jsonl").is_file()
        return result

    try:
        session.ensure()
        monkeypatch.setenv("AGENT_CODEX_SDK_SESSION_SOCKET", str(session.socket_path))
        first = invoke("implementation-agent", f"Remember this marker for the next turn: {marker}. Put exactly the marker in summary.", 1)
        assert marker in first["summary"]
        session.close()
        session.ensure()
        continued = invoke("implementation-agent", "Recall the marker from the previous turn of this conversation and put exactly that marker in summary.", 2)
        assert continued["thread_id"] == first["thread_id"]
        assert marker in continued["summary"]
        reviewed = invoke("reviewer", "This is a standalone independent-review transport check. Put 'Independent review smoke' in summary.", 3)
        assert reviewed["thread_id"] != first["thread_id"]
        assert session.state()["threads"][run_id] == first["thread_id"]
    finally:
        session.close()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=True)
    assert status.stdout == ""
