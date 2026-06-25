from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "codex_adapter.py"
SPEC = importlib.util.spec_from_file_location("codex_adapter_smoke", MODULE_PATH)
assert SPEC is not None
codex_adapter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = codex_adapter
SPEC.loader.exec_module(codex_adapter)


@pytest.mark.skipif(
    os.environ.get("AGENT_REAL_CODEX_SMOKE") != "1" or not os.environ.get("AGENT_CODEX_CLI_COMMAND"),
    reason="optional real Codex smoke requires AGENT_REAL_CODEX_SMOKE=1 and AGENT_CODEX_CLI_COMMAND",
)
def test_real_codex_runtime_smoke(tmp_path: Path) -> None:
    manifest = tmp_path / "context.json"
    raw_dir = tmp_path / "raw"
    manifest.write_text(
        json.dumps(
            {
                "run_id": "real-smoke",
                "role": "planner",
                "goal": "Return a valid role_result JSON object for smoke testing.",
                "repository": str(tmp_path),
                "artifacts_dir": str(tmp_path / "artifacts"),
                "project": "agent_workspace",
                "project_profile": "agent_workspace",
                "token_budget": 1000,
                "allowed_tools": ["filesystem_read"],
                "filesystem_access": "read_only",
                "prompt_path": ".agents/prompts/planner.md",
                "output_contract": "schemas/roles/planner.schema.json",
                "expected_artifacts": ["plan.md"],
                "created_at": "2026-06-25T00:00:00+00:00",
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
        "goal": "Return a valid role_result JSON object for smoke testing.",
        "repository": str(tmp_path),
        "artifacts_dir": str(tmp_path / "artifacts"),
        "context_manifest": str(manifest),
        "prompt_path": ".agents/prompts/planner.md",
        "output_contract": "schemas/roles/planner.schema.json",
        "project_profile": "agent_workspace",
        "expected_artifacts": ["plan.md"],
        "allowed_tools": ["filesystem_read"],
        "filesystem_access": "read_only",
        "token_budget": 1000,
        "timeout_seconds": 60,
    }

    executor = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "codex_cli_executor.py"
    completed = subprocess.run(
        [sys.executable, str(executor)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )

    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["status"] in {"completed", "blocked", "failed", "awaiting_approval"}
