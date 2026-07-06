#!/usr/bin/env python3
"""Minimal executable workflow runner with bounded retries and trace storage."""

from __future__ import annotations

import argparse
import json
import random
import shlex
import string
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".agent-workflows.yaml"
RUNS_DIR = ROOT / ".agent-runs"
DEFAULT_TIMEOUT_SECONDS = 300


@dataclass
class StepResult:
    name: str
    command: str
    attempt: int
    returncode: int
    stdout: str
    stderr: str

    def as_json(self) -> dict[str, Any]:
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "step": self.name,
            "command": self.command,
            "attempt": self.attempt,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def run_command(command: str, cwd: Path, timeout_seconds: int) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            shlex.split(command),
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"command timed out after {timeout_seconds}s"
    except PermissionError as exc:
        return 126, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def read_workflows(path: Path | None = None) -> dict[str, Any]:
    workflow_path = path or WORKFLOWS
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(".agent-workflows.yaml must contain an object")
    return data


def make_run_id(workflow_name: str) -> str:
    suffix = "".join(random.SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(6))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{timestamp}-{workflow_name}-{suffix}"


def make_run_dir(workflow_name: str, run_id: str = "") -> Path:
    run_id = run_id or make_run_id(workflow_name)
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def append_trace(run_dir: Path, event: dict[str, Any]) -> None:
    with (run_dir / "workflow_trace.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def workflow_steps(workflow: dict[str, Any]) -> list[dict[str, str]]:
    steps = workflow.get("steps", [])
    if isinstance(steps, list) and steps:
        return [step for step in steps if isinstance(step, dict)]
    executor = workflow.get("executor")
    if isinstance(executor, str) and executor:
        return [{"name": "executor", "command": executor}]
    return []


def quote_placeholder(value: str | Path) -> str:
    return shlex.quote(str(value))


def run_workflow(
    workflow_name: str,
    dry_run: bool = False,
    root: Path = ROOT,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    run_id: str = "",
    task_id: str = "task",
    project: str = "",
    repository: Path | None = None,
    branch: str = "",
    base_branch: str = "main",
    adapter_command: str = "",
) -> int:
    workflows = read_workflows()
    workflow = workflows.get("workflows", {}).get(workflow_name)
    if not isinstance(workflow, dict):
        print(f"unknown workflow: {workflow_name}")
        return 2
    run_id = run_id or make_run_id(workflow_name)
    run_dir = make_run_dir(workflow_name, run_id)
    project_value = project or "agent_workspace"
    branch_value = branch or f"issue/{task_id}"
    repository_value = (repository or root).resolve()
    max_iterations = int(workflow.get("max_iterations", 1))
    workflow_timeout_seconds = int(workflow.get("timeout_seconds", timeout_seconds))
    retry = workflow.get("retry", {})
    max_retries = int(retry.get("max_retries", 0)) if isinstance(retry, dict) else 0
    backoff_seconds = float(retry.get("backoff_seconds", 0)) if isinstance(retry, dict) else 0.0
    steps = workflow_steps(workflow)
    effective_adapter_command = adapter_command or str(workflow.get("adapter_command", ""))
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    append_trace(
        run_dir,
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": "workflow_started",
            "workflow": workflow_name,
            "dry_run": dry_run,
            "max_iterations": max_iterations,
        },
    )
    for iteration in range(1, max_iterations + 1):
        append_trace(run_dir, {"time": datetime.now(timezone.utc).isoformat(), "event": "iteration_started", "iteration": iteration})
        for step in steps:
            name = str(step.get("name", "step"))
            command = str(step.get("command", ""))
            command = (
                command.replace("{run_id}", quote_placeholder(run_id))
                .replace("{run_dir}", quote_placeholder(run_dir))
                .replace("{artifacts_dir}", quote_placeholder(artifacts_dir))
                .replace("{task_id}", quote_placeholder(task_id))
                .replace("{project}", quote_placeholder(project_value))
                .replace("{repository}", quote_placeholder(repository_value))
                .replace("{branch}", quote_placeholder(branch_value))
                .replace("{base_branch}", quote_placeholder(base_branch))
                .replace("{adapter_command}", quote_placeholder(effective_adapter_command))
            )
            if (
                effective_adapter_command
                and command.startswith("python3 scripts/agent_role_runner.py")
                and "--adapter-command" not in command
            ):
                command = f"{command} --adapter-command {quote_placeholder(effective_adapter_command)}"
            if (
                dry_run
                and command.startswith(("python3 scripts/publish_pr.py", "python3 scripts/agent_role_runner.py"))
                and "--dry-run" not in command
            ):
                command = command + " --dry-run"
            for attempt in range(1, max_retries + 2):
                returncode, stdout, stderr = run_command(command, root, workflow_timeout_seconds)
                result = StepResult(name, command, attempt, returncode, stdout, stderr)
                append_trace(run_dir, result.as_json())
                if returncode == 0:
                    break
                if attempt <= max_retries:
                    time.sleep(backoff_seconds)
            if returncode != 0:
                append_trace(
                    run_dir,
                    {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "event": "workflow_failed",
                        "step": name,
                        "returncode": returncode,
                    },
                )
                print(str(run_dir))
                return returncode
    append_trace(run_dir, {"time": datetime.now(timezone.utc).isoformat(), "event": "workflow_completed"})
    print(str(run_dir))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", nargs="?", default="publish_pr")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--task-id", default="task")
    parser.add_argument("--project", default="agent_workspace")
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--branch", default="")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--adapter-command", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_workflow(
        args.workflow,
        dry_run=args.dry_run,
        timeout_seconds=args.timeout_seconds,
        run_id=args.run_id,
        task_id=args.task_id,
        project=args.project,
        repository=args.repo,
        branch=args.branch,
        base_branch=args.base_branch,
        adapter_command=args.adapter_command,
    )


if __name__ == "__main__":
    raise SystemExit(main())
