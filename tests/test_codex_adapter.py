from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "codex_adapter.py"
SPEC = importlib.util.spec_from_file_location("codex_adapter", MODULE_PATH)
assert SPEC is not None
codex_adapter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = codex_adapter
SPEC.loader.exec_module(codex_adapter)


def role_request(tmp_path: Path) -> dict[str, object]:
    manifest = tmp_path / "context.json"
    manifest.write_text("{}", encoding="utf-8")
    return {
        "run_id": "run-1",
        "role": "planner",
        "goal": "Test",
        "repository": str(tmp_path),
        "artifacts_dir": str(tmp_path / "artifacts"),
        "context_manifest": str(manifest),
        "allowed_tools": ["filesystem_read"],
        "token_budget": 12000,
        "timeout_seconds": 30,
    }


def test_codex_adapter_blocks_when_no_command_configured(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.delenv("AGENT_CODEX_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_LLM_COMMAND", raising=False)
    adapter = codex_adapter.CodexAdapter(raw_output_dir=tmp_path / "raw")

    result = adapter.invoke(role_request(tmp_path))

    assert result["status"] == "blocked"
    assert "No Codex adapter command configured." == result["summary"]


def test_codex_adapter_runs_command_and_validates_result(tmp_path: Path) -> None:
    script = tmp_path / "adapter.py"
    script.write_text(
        """
import json
import sys

request = json.loads(sys.stdin.read())
print(json.dumps({
    "status": "completed",
    "next_action": "risk-classifier",
    "summary": request["role"] + " ok",
    "artifacts_created": ["plan.md"],
    "blockers": [],
    "warnings": [],
    "tokens_used": 42
}))
""".lstrip(),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    adapter = codex_adapter.CodexAdapter(
        command=f"{sys.executable} {script}",
        raw_output_dir=tmp_path / "raw",
    )

    result = adapter.invoke(role_request(tmp_path))

    assert result["status"] == "completed"
    assert result["next_action"] == "risk-classifier"
    raw = json.loads((tmp_path / "raw" / "planner.json").read_text(encoding="utf-8"))
    assert raw["returncode"] == 0
