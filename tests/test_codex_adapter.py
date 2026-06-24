from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
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
        "prompt_path": ".agents/prompts/planner.md",
        "output_contract": "schemas/roles/planner.schema.json",
        "project_profile": "agent_workspace",
        "expected_artifacts": ["plan.md"],
        "allowed_tools": ["filesystem_read"],
        "filesystem_access": "read_only",
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

    assert result["status"] == "completed", result
    assert result["next_action"] == "risk-classifier"
    assert isinstance(result["duration_ms"], int)
    raw = json.loads((tmp_path / "raw" / "planner.json").read_text(encoding="utf-8"))
    assert raw["returncode"] == 0


def test_codex_cli_executor_loads_inputs_and_returns_structured_result(tmp_path: Path, monkeypatch: object) -> None:
    executor = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "codex_cli_executor.py"
    cli = tmp_path / "fake_codex_cli.py"
    cli.write_text(
        """
import json
import os
import sys

prompt = sys.stdin.read()
assert "Role execution request:" in prompt
assert os.environ["AGENT_ROLE"] == "planner"
assert os.environ["AGENT_ROLE_FILESYSTEM_ACCESS"] == "read_only"
print(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": "planner done",
    "artifacts_created": ["plan.md"],
    "blockers": [],
    "warnings": [],
    "tokens_used": 5
}))
""".lstrip(),
        encoding="utf-8",
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    manifest = tmp_path / "context.json"
    request = role_request(tmp_path)
    manifest.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "role": "planner",
                "goal": "Test",
                "repository": str(tmp_path),
                "artifacts_dir": str(tmp_path / "artifacts"),
                "project": "agent_workspace",
                "project_profile": "agent_workspace",
                "token_budget": 12000,
                "allowed_tools": ["filesystem_read"],
                "filesystem_access": "read_only",
                "prompt_path": ".agents/prompts/planner.md",
                "output_contract": "schemas/roles/planner.schema.json",
                "expected_artifacts": ["plan.md"],
                "created_at": "2026-06-24T00:00:00+00:00",
                "context_files": [],
                "artifact_references": [],
                "skill_references": [],
                "previous_roles": [],
                "retrieval_rules": [],
                "raw_outputs_dir": str(tmp_path / "raw"),
            }
        ),
        encoding="utf-8",
    )
    request["context_manifest"] = str(manifest)
    monkeypatch.setenv("AGENT_CODEX_CLI_COMMAND", f"{sys.executable} {cli}")

    completed = subprocess.run(
        [sys.executable, str(executor)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["status"] == "completed", result
    assert result["summary"] == "planner done"
