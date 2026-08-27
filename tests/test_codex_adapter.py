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

EXECUTOR_PATH = MODULE_PATH.with_name("codex_cli_executor.py")
EXECUTOR_SPEC = importlib.util.spec_from_file_location("codex_cli_executor", EXECUTOR_PATH)
assert EXECUTOR_SPEC is not None
codex_cli_executor = importlib.util.module_from_spec(EXECUTOR_SPEC)
assert EXECUTOR_SPEC.loader is not None
sys.modules[EXECUTOR_SPEC.name] = codex_cli_executor
EXECUTOR_SPEC.loader.exec_module(codex_cli_executor)


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


def test_compiled_context_package_is_the_only_reference_read(tmp_path: Path) -> None:
    package = tmp_path / "package.md"
    direct_reference = tmp_path / "direct.md"
    package.write_text("compiled-safe-context", encoding="utf-8")
    direct_reference.write_text("must-not-be-read-directly", encoding="utf-8")
    manifest = context_manifest_payload(tmp_path)
    manifest["context_package_path"] = str(package)
    manifest["context_files"] = [{"path": str(direct_reference), "kind": "policy"}]
    manifest["skill_references"] = [
        {"name": "unsafe-direct-skill", "path": str(direct_reference)}
    ]

    contents = codex_cli_executor.context_reference_contents(manifest)

    assert "compiled-safe-context" in contents
    assert "must-not-be-read-directly" not in contents


def test_role_prompt_includes_interaction_policy_and_recorded_user_answer(tmp_path: Path) -> None:
    request = role_request(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (tmp_path / "human-input.json").write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": "run-1",
                "entries": [
                    {
                        "question_id": "environment_choice",
                        "response": "Use the staging environment.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    prompt = codex_cli_executor.role_prompt_payload(
        request=request,
        prompt_text="Planner prompt",
        manifest=context_manifest_payload(tmp_path),
        output_contract={"required": [], "types": {}},
    )

    assert "Use the staging environment." in prompt
    assert "[environment_choice]" in prompt
    assert "do not perform empty retries" in prompt
    assert "status=awaiting_approval" in prompt
    assert "2-3 mutually exclusive options" in prompt
    assert "requires_input=true" in prompt
    assert "never ask a substantially identical question again" in prompt

    schema = codex_cli_executor.standard_role_result_schema({"required": [], "types": {}})
    assert "question" in schema["properties"]
    assert "question" in schema["required"]
    assert "child_tasks" in schema["properties"]
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["question"]["type"] == ["object", "null"]
    assert schema["properties"]["question"]["properties"]["requirement"] == {"type": "string"}
    assert "requirement" in schema["properties"]["question"]["required"]
    assert schema["properties"]["question"]["properties"]["options"]["type"] == "array"
    assert schema["properties"]["question"]["properties"]["options"]["minItems"] == 2
    assert schema["properties"]["question"]["properties"]["options"]["maxItems"] == 3
    assert "child_tasks" in schema["required"]
    assert set(schema["required"]) == set(schema["properties"])


def test_noncompleted_role_result_gets_a_nonempty_attention_reason() -> None:
    result = codex_cli_executor.parse_role_result(
        json.dumps(
            {
                "status": "awaiting_approval",
                "next_action": "awaiting_approval",
                "summary": "Which environment should be used?",
                "artifacts_created": [],
                "blockers": [],
                "warnings": [],
                "tokens_used": 1,
            }
        ),
        codex_cli_executor.load_json(
            Path(__file__).resolve().parents[1] / "schemas" / "role_result.schema.json"
        ),
        1,
    )

    assert result["status"] == "awaiting_approval"
    assert result["blockers"] == ["Which environment should be used?"]


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
    assert result["tokens_used"] == 12
    assert result["input_tokens"] == 10
    assert result["cached_input_tokens"] == 2
    assert result["output_tokens"] == 4
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
    assert result["summary"] == "Structured output repair budget exhausted."
    assert result["_failure"]["repair_attempts"] == 2


def test_codex_cli_executor_repairs_missing_artifact_without_role_rerun(
    tmp_path: Path, monkeypatch: object
) -> None:
    executor = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "codex_cli_executor.py"
    cli = tmp_path / "fake_codex_cli.py"
    cli.write_text(
        """
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
is_repair = "-repair-" in result_path.name
payload = {
    "status": "completed", "next_action": "continue", "summary": "planner done",
    "artifacts_created": [], "artifacts": [], "blockers": [], "warnings": [], "tokens_used": 5
}
if is_repair:
    payload["artifacts"] = [{"path": "plan.md", "content": "# repaired plan\\n"}]
result_path.write_text(json.dumps(payload), encoding="utf-8")
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
        [sys.executable, str(executor)], input=json.dumps(request), text=True,
        capture_output=True, check=False,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "completed"
    assert result["output_repair_attempts"] == 1
    assert (tmp_path / "artifacts" / "plan.md").read_text(encoding="utf-8") == "# repaired plan\n"


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
