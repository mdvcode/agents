from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ai_harness.recovery.output_repair import repair_output


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from adapters import codex_cli_executor  # noqa: E402


SCHEMA = {"required": ["status"], "types": {"status": "str"}}


def validate(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ["malformed JSON"]
    return [] if isinstance(parsed, dict) and isinstance(parsed.get("status"), str) else ["missing status"]


def test_invalid_json_is_repaired_without_reexecuting_task_context() -> None:
    prompts: list[str] = []

    def invoke(prompt: str) -> str:
        prompts.append(prompt)
        return '{"status":"completed"}'

    result = repair_output(
        original_output="{bad",
        schema=SCHEMA,
        validation_errors=["malformed JSON"],
        invoke=invoke,
        validate=validate,
    )
    assert result.repaired is True
    assert result.attempts == 1
    assert "Original structured output:" in prompts[0]
    assert "Context manifest" not in prompts[0]


def test_output_repair_exhaustion_is_bounded() -> None:
    result = repair_output(
        original_output="{bad",
        schema=SCHEMA,
        validation_errors=["malformed JSON"],
        invoke=lambda _prompt: "still bad",
        validate=validate,
        max_attempts=2,
    )
    assert result.repaired is False
    assert result.attempts == 2
    assert result.errors


def test_codex_executor_repairs_invalid_structured_output_in_place(tmp_path: Path, monkeypatch: object) -> None:
    prompts: list[str] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 1, "", "not a git repository")
        prompt = str(kwargs["input"])
        prompts.append(prompt)
        result_path = Path(args[args.index("--output-last-message") + 1])
        if len(prompts) == 1:
            result_path.write_text("{bad", encoding="utf-8")
        else:
            result_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "next_action": "continue",
                        "summary": "repaired",
                        "artifacts_created": [],
                        "artifacts": [],
                        "blockers": [],
                        "warnings": [],
                        "tokens_used": 1,
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(codex_cli_executor, "configured_codex_base_command", lambda: ["codex"])
    monkeypatch.setattr(codex_cli_executor.subprocess, "run", fake_run)
    request = {
        "run_id": "run-1",
        "role": "planner",
        "repository": str(tmp_path),
        "artifacts_dir": str(tmp_path / "artifacts"),
        "filesystem_access": "task_worktree_write",
        "allowed_tools": [],
    }
    manifest = {"raw_outputs_dir": str(tmp_path / "raw")}
    contract = {
        "required": ["status", "next_action", "summary", "artifacts_created", "blockers", "warnings", "tokens_used"],
        "types": {"status": "str", "next_action": "str", "summary": "str", "artifacts_created": "list", "blockers": "list", "warnings": "list", "tokens_used": "int"},
    }

    result = codex_cli_executor.run_codex(
        request=request,
        prompt="ordinary role task",
        timeout_seconds=10,
        output_contract=contract,
        manifest=manifest,
    )

    assert result["status"] == "completed"
    assert result["output_repair_attempts"] == 1
    assert len(prompts) == 2
    assert prompts[1].startswith("Original structured output:")
    assert "ordinary role task" not in prompts[1]
