from __future__ import annotations

import importlib.util
import json
import shlex
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
    traces = list((tmp_path / ".agent-runs").glob("*/raw-events/workflow-runner.jsonl"))
    assert len(traces) == 1
    events = [json.loads(line) for line in traces[0].read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "workflow_completed"
    assert any(event.get("step") == "ok" and event.get("returncode") == 0 for event in events)
    otel_paths = list((tmp_path / ".agent-runs").glob("*/raw-events/otel-spans.jsonl"))
    assert len(otel_paths) == 1
    spans = [json.loads(line) for line in otel_paths[0].read_text(encoding="utf-8").splitlines()]
    assert {span["name"] for span in spans} == {"ai_harness.workflow", "ai_harness.workflow.step"}
    root = next(span for span in spans if span["name"] == "ai_harness.workflow")
    step = next(span for span in spans if span["name"] == "ai_harness.workflow.step")
    assert step["trace_id"] == root["trace_id"]
    assert step["parent_span_id"] == root["span_id"]


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


def test_workflow_runner_does_not_retry_after_authoritative_attention_pause(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    max_iterations: 1
    retry:
      max_retries: 3
      backoff_seconds: 0
    steps:
      - name: "planner"
        command: "role-command"
""".lstrip(),
        encoding="utf-8",
    )
    runs = tmp_path / ".agent-runs"
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", runs)
    calls = 0

    def pause_for_question(_command: str, _cwd: Path, _timeout: int) -> tuple[int, str, str]:
        nonlocal calls
        calls += 1
        workflow_path = runs / "run-question" / "workflow.json"
        state = json.loads(workflow_path.read_text(encoding="utf-8"))
        state.update(
            {
                "execution_status": "awaiting_approval",
                "recovery_action": "approval",
                "blockers": ["Which region should be used?"],
            }
        )
        workflow_path.write_text(json.dumps(state), encoding="utf-8")
        return 10, "", "input required"

    monkeypatch.setattr(run_workflow, "run_command", pause_for_question)

    result = run_workflow.run_workflow(
        "sample",
        root=tmp_path,
        run_id="run-question",
        task_id="question-task",
    )

    assert result == run_workflow.EXIT_AWAITING_APPROVAL
    assert calls == 1


def test_timeout_creates_failure_record_and_returns_retryable_exit(tmp_path: Path, monkeypatch: object) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    max_iterations: 1
    steps:
      - name: "implementation-agent"
        command: "codex-role"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", tmp_path / ".agent-runs")
    monkeypatch.setattr(
        run_workflow,
        "run_command",
        lambda _command, _cwd, _timeout: (124, "", "command timed out"),
    )

    result = run_workflow.run_workflow("sample", root=tmp_path, run_id="run-timeout", task_id="task-timeout")

    assert result == run_workflow.EXIT_RETRYABLE_FAILURE
    state = json.loads((tmp_path / ".agent-runs" / "run-timeout" / "workflow.json").read_text(encoding="utf-8"))
    assert state["execution_status"] == "retry_wait"
    assert state["failure_kind"] == "transient"
    assert state["resume_role"] == "implementation-agent"
    assert len(list((tmp_path / ".agent-runs" / "run-timeout" / "failures").glob("*.json"))) == 1
    spans = [
        json.loads(line)
        for line in (
            tmp_path / ".agent-runs" / "run-timeout" / "raw-events" / "otel-spans.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    names = {span["name"] for span in spans}
    assert "ai_harness.recovery.classify" in names
    assert "ai_harness.recovery.retry" in names


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


def test_workflow_runner_passes_quoted_goal_to_agent_role_runner(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    steps:
      - name: "roles"
        command: "python3 scripts/agent_role_runner.py --workflow sample --task-id {task_id} --goal {goal}"
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
    goal = "Add office photos — don't expand $(touch /tmp/pwned)?"

    result = run_workflow.run_workflow(
        "sample",
        root=tmp_path,
        task_id="photo-services",
        goal=goal,
    )

    assert result == 0
    assert len(commands) == 1
    assert shlex.split(commands[0])[-2:] == ["--goal", goal]


def test_workflow_runner_passes_current_branch_mode_to_agent_role_runner(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    steps:
      - name: "roles"
        command: "python3 scripts/agent_role_runner.py --workflow sample"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", tmp_path / ".agent-runs")
    commands: list[str] = []
    monkeypatch.setattr(
        run_workflow,
        "run_command",
        lambda command, _cwd, _timeout: (commands.append(command) or 0, "ok", ""),
    )

    result = run_workflow.run_workflow("sample", root=tmp_path, current_branch=True)

    assert result == 0
    assert shlex.split(commands[0])[-1] == "--current-branch"


def test_workflow_runner_cli_accepts_goal(monkeypatch: object) -> None:
    goal = "Add service background photos"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_workflow.py", "full_agent_workflow", "--goal", goal],
    )

    args = run_workflow.parse_args()

    assert args.goal == goal


def test_unknown_workflow_uses_invalid_harness_state_exit(tmp_path: Path, monkeypatch: object) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text("version: 1\nworkflows: {}\n", encoding="utf-8")
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    assert run_workflow.run_workflow("missing", root=tmp_path) == run_workflow.EXIT_INVALID_HARNESS_STATE


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


def test_workflow_runner_deduplicates_completed_task_input(tmp_path: Path, monkeypatch: object) -> None:
    workflows_path = tmp_path / ".agent-workflows.yaml"
    workflows_path.write_text(
        """
version: 1
workflows:
  sample:
    steps:
      - name: "must-not-run"
        command: "false"
""".lstrip(),
        encoding="utf-8",
    )
    runs = tmp_path / ".agent-runs"
    completed = runs / "completed"
    completed.mkdir(parents=True)
    fingerprint = run_workflow.task_fingerprint(
        task_id="same-task",
        goal="same-task",
        repository=tmp_path,
        branch="issue/same-task",
        base_branch="main",
    )
    (completed / "workflow.json").write_text(
        json.dumps(
            {
                "run_id": "completed",
                "input_fingerprint": fingerprint,
                "execution_status": "completed",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_workflow, "WORKFLOWS", workflows_path)
    monkeypatch.setattr(run_workflow, "RUNS_DIR", runs)
    assert run_workflow.run_workflow("sample", root=tmp_path, task_id="same-task") == 0
    assert sorted(path.name for path in runs.iterdir()) == ["completed"]
