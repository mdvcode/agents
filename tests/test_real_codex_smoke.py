from __future__ import annotations

import importlib.util
import os
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
    os.environ.get("AGENT_REAL_CODEX_SMOKE") != "1" or not os.environ.get("AGENT_CODEX_COMMAND"),
    reason="optional real Codex smoke requires AGENT_REAL_CODEX_SMOKE=1 and AGENT_CODEX_COMMAND",
)
def test_real_codex_runtime_smoke(tmp_path: Path) -> None:
    manifest = tmp_path / "context.json"
    manifest.write_text("{}", encoding="utf-8")
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

    result = codex_adapter.CodexAdapter(raw_output_dir=tmp_path / "raw").invoke(request)

    assert result["status"] in {"completed", "blocked", "failed", "awaiting_approval"}
