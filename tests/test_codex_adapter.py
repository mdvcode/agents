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


def context_manifest_payload(tmp_path: Path, raw_dir: Path | None = None) -> dict[str, object]:
    return {
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
        "raw_outputs_dir": str(raw_dir or tmp_path / "raw"),
    }


def test_json_contract_supports_nullable_nested_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "loop": {
                "type": ["object", "null"],
                "properties": {"iteration": {"type": "integer"}},
                "required": ["iteration"],
                "additionalProperties": False,
            }
        },
        "required": ["loop"],
        "additionalProperties": False,
    }

    assert codex_adapter.validate_contract({"loop": None}, schema, "route") == []
    assert codex_adapter.validate_contract({"loop": {"iteration": 1}}, schema, "route") == []
    assert codex_adapter.validate_contract({"loop": {}}, schema, "route") == [
        "route.loop: missing required field 'iteration'"
    ]


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
from pathlib import Path

prompt = sys.stdin.read()
assert sys.argv[1] == "exec"
assert "--json" in sys.argv
assert "--sandbox" in sys.argv
assert sys.argv[sys.argv.index("--sandbox") + 1] == "read-only"
schema_path = Path(sys.argv[sys.argv.index("--output-schema") + 1])
assert schema_path.exists()
result_path = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
assert "Role execution request:" in prompt
assert os.environ["AGENT_ROLE"] == "planner"
assert os.environ["AGENT_ROLE_FILESYSTEM_ACCESS"] == "read_only"
result_path.write_text(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": "planner done",
    "artifacts_created": [],
    "artifacts": [{"path": "plan.md", "content": "# Plan\\n"}],
    "blockers": [],
    "warnings": [],
    "tokens_used": 5
}), encoding="utf-8")
print(json.dumps({"type": "thread.started", "thread_id": "thread-test"}))
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 10,
    "cached_input_tokens": 2,
    "output_tokens": 4,
    "reasoning_output_tokens": 1
}}))
""".lstrip(),
        encoding="utf-8",
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    manifest = tmp_path / "context.json"
    request = role_request(tmp_path)
    manifest.write_text(json.dumps(context_manifest_payload(tmp_path)), encoding="utf-8")
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
    assert result["thread_id"] == "thread-test"
    assert result["tokens_used"] == 14
    assert (tmp_path / "artifacts" / "plan.md").read_text(encoding="utf-8") == "# Plan\n"


def test_codex_cli_executor_blocks_when_required_artifact_missing(tmp_path: Path, monkeypatch: object) -> None:
    executor = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "codex_cli_executor.py"
    cli = tmp_path / "fake_codex_cli.py"
    cli.write_text(
        """
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
result_path.write_text(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": "planner done",
    "artifacts_created": ["plan.md"],
    "artifacts": [],
    "blockers": [],
    "warnings": [],
    "tokens_used": 5
}), encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    manifest = tmp_path / "context.json"
    request = role_request(tmp_path)
    manifest.write_text(json.dumps(context_manifest_payload(tmp_path)), encoding="utf-8")
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
    assert result["status"] == "blocked"
    assert result["summary"] == "Codex CLI completed without required artifacts."


def test_codex_cli_executor_blocks_read_only_repository_mutation(tmp_path: Path, monkeypatch: object) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True, text=True)

    executor = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "codex_cli_executor.py"
    cli = tmp_path / "fake_codex_cli.py"
    cli.write_text(
        """
import json
import sys
from pathlib import Path

Path("tracked.txt").write_text("after\\n", encoding="utf-8")
result_path = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
result_path.write_text(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": "planner done",
    "artifacts_created": [],
    "artifacts": [{"path": "plan.md", "content": "# Plan\\n"}],
    "blockers": [],
    "warnings": [],
    "tokens_used": 5
}), encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    manifest = tmp_path / "context.json"
    request = role_request(tmp_path)
    manifest.write_text(json.dumps(context_manifest_payload(tmp_path)), encoding="utf-8")
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
    assert result["status"] == "blocked"
    assert result["summary"] == "Read-only role changed repository contents."


def run_executor_for_artifact(
    tmp_path: Path,
    monkeypatch: object,
    *,
    role: str,
    expected_artifacts: list[str],
    artifact_path: str,
    output_contract: str = "schemas/role_result.schema.json",
) -> dict[str, object]:
    executor = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "codex_cli_executor.py"
    cli = tmp_path / f"fake_{role}.py"
    cli.write_text(
        f"""
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
result_path.write_text(json.dumps({{
    "status": "completed",
    "next_action": "continue",
    "summary": "{role} done",
    "artifacts_created": [],
    "artifacts": [
        {{"path": "plan.md", "content": "# Plan\\n"}},
        {{"path": "{artifact_path}", "content": "{{}}"}}
    ],
    "blockers": [],
    "warnings": [],
    "tokens_used": 5
}}), encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    manifest = tmp_path / f"{role}.context.json"
    manifest_payload = context_manifest_payload(tmp_path)
    manifest_payload["role"] = role
    manifest_payload["expected_artifacts"] = expected_artifacts
    manifest_payload["output_contract"] = output_contract
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    request = role_request(tmp_path)
    request["role"] = role
    request["context_manifest"] = str(manifest)
    request["output_contract"] = output_contract
    request["expected_artifacts"] = expected_artifacts
    monkeypatch.setenv("AGENT_CODEX_CLI_COMMAND", f"{sys.executable} {cli}")

    completed = subprocess.run(
        [sys.executable, str(executor)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    return json.loads(completed.stdout)


def test_codex_cli_executor_blocks_planner_from_writing_verdict(tmp_path: Path, monkeypatch: object) -> None:
    result = run_executor_for_artifact(
        tmp_path,
        monkeypatch,
        role="planner",
        expected_artifacts=["plan.md"],
        artifact_path="verdict.json",
        output_contract="schemas/roles/planner.schema.json",
    )

    assert result["status"] == "blocked"
    assert "planner cannot write verdict.json" in "\n".join(result["blockers"])
    assert not (tmp_path / "artifacts" / "verdict.json").exists()


def test_codex_cli_executor_blocks_risk_classifier_from_writing_quality(tmp_path: Path, monkeypatch: object) -> None:
    result = run_executor_for_artifact(
        tmp_path,
        monkeypatch,
        role="risk-classifier",
        expected_artifacts=["risk.json"],
        artifact_path="quality.json",
        output_contract="schemas/roles/risk-classifier.schema.json",
    )

    assert result["status"] == "blocked"
    assert "risk-classifier cannot write plan.md" in "\n".join(result["blockers"])
    assert "risk-classifier cannot write quality.json" in "\n".join(result["blockers"])


def test_codex_cli_executor_allows_orchestrator_verdict(tmp_path: Path, monkeypatch: object) -> None:
    result = run_executor_for_artifact(
        tmp_path,
        monkeypatch,
        role="orchestrator",
        expected_artifacts=["plan.md", "verdict.json"],
        artifact_path="verdict.json",
        output_contract="schemas/roles/orchestrator.schema.json",
    )

    assert result["status"] == "completed", result
    assert (tmp_path / "artifacts" / "verdict.json").read_text(encoding="utf-8") == "{}"
