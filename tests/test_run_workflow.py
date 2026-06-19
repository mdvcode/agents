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
