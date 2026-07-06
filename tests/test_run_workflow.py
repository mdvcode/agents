from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_workflow.py"
SPEC = importlib.util.spec_from_file_location("run_workflow", MODULE_PATH)
assert SPEC is not None
run_workflow = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = run_workflow
SPEC.loader.exec_module(run_workflow)


def test_workflow_runner_writes_trace_store(tmp_path: Path, monkeypatch: object) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    max_iterations: 1
    retry:
      max_retries: 0
      backoff_seconds: 0
    steps:
      - name: "ok"
        command: "python3 -c 'print(42)'"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", tmp_path / ".agent-runs")

    result = run_workflow.run_workflow("sample", root=tmp_path)

    assert result == 0
    traces = list((tmp_path / ".agent-runs").glob("*/workflow_trace.jsonl"))
    assert len(traces) == 1
    events = [json.loads(line) for line in traces[0].read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "workflow_completed"
    assert any(event.get("step") == "ok" and event.get("returncode") == 0 for event in events)


def test_workflow_runner_expands_run_scoped_artifacts_dir(tmp_path: Path, monkeypatch: object) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    max_iterations: 1
    retry:
      max_retries: 0
      backoff_seconds: 0
    steps:
      - name: "write-artifact"
        command: >-
          python3 -c 'from pathlib import Path; import sys; Path(sys.argv[1], "marker.txt").write_text("ok", encoding="utf-8")' {artifacts_dir}
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", tmp_path / ".agent-runs")

    result = run_workflow.run_workflow("sample", root=tmp_path)

    assert result == 0
    markers = list((tmp_path / ".agent-runs").glob("*/artifacts/marker.txt"))
    assert len(markers) == 1
    assert markers[0].read_text(encoding="utf-8") == "ok"


def test_workflow_runner_uses_workflow_timeout(tmp_path: Path, monkeypatch: object) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    max_iterations: 1
    timeout_seconds: 17
    steps:
      - name: "ok"
        command: "python3 -c 'print(42)'"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", tmp_path / ".agent-runs")
    calls: list[int] = []

    def fake_run_command(command: str, cwd: Path, timeout_seconds: int) -> tuple[int, str, str]:
        calls.append(timeout_seconds)
        return 0, "ok", ""

    monkeypatch.setattr(run_workflow, "run_command", fake_run_command)

    result = run_workflow.run_workflow("sample", root=tmp_path, timeout_seconds=3)

    assert result == 0
    assert calls == [17]


def test_workflow_runner_passes_adapter_command_from_workflow(tmp_path: Path, monkeypatch: object) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    adapter_command: "python3 scripts/adapters/codex_cli_executor.py"
    max_iterations: 1
    steps:
      - name: "roles"
        command: "python3 scripts/agent_role_runner.py --workflow sample --adapter-command {adapter_command}"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", tmp_path / ".agent-runs")
    commands: list[str] = []

    def fake_run_command(command: str, cwd: Path, timeout_seconds: int) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "ok", ""

    monkeypatch.setattr(run_workflow, "run_command", fake_run_command)

    result = run_workflow.run_workflow("sample", root=tmp_path)

    assert result == 0
    assert commands == [
        "python3 scripts/agent_role_runner.py --workflow sample --adapter-command "
        "'python3 scripts/adapters/codex_cli_executor.py'"
    ]


def test_workflow_runner_cli_adapter_command_overrides_workflow(tmp_path: Path, monkeypatch: object) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    adapter_command: "default-adapter"
    max_iterations: 1
    steps:
      - name: "roles"
        command: "python3 scripts/agent_role_runner.py --workflow sample"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", tmp_path / ".agent-runs")
    commands: list[str] = []

    def fake_run_command(command: str, cwd: Path, timeout_seconds: int) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "ok", ""

    monkeypatch.setattr(run_workflow, "run_command", fake_run_command)

    result = run_workflow.run_workflow("sample", root=tmp_path, adapter_command="custom-adapter")

    assert result == 0
    assert commands == [
        "python3 scripts/agent_role_runner.py --workflow sample --adapter-command custom-adapter"
    ]
