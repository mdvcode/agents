#!/usr/bin/env python3
"""Deterministic executable agent-role workflow scaffold."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / ".agents" / "prompts"
RUNS = ROOT / ".agent-runs"
ROLE_CHAIN = [
    "issue-intake",
    "context-compiler",
    "planner",
    "risk-classifier",
    "implementation-agent",
    "test-generator",
    "quality-runner",
    "security-agent",
    "frontend-qa-agent",
    "architecture-consistency-agent",
    "semantic-conflict-agent",
    "reviewer",
    "ci-repair-agent",
    "orchestrator",
    "eval-runner",
    "report-agent",
    "publication",
]
PROMPT_FILES = {
    "context-compiler": "context-compiler.md",
    "planner": "planner.md",
    "risk-classifier": "risk-classifier.md",
    "implementation-agent": "implementation-agent.md",
    "test-generator": "test-generator.md",
    "quality-runner": "quality-runner.md",
    "security-agent": "security-agent.md",
    "frontend-qa-agent": "frontend-qa-agent.md",
    "architecture-consistency-agent": "architecture-consistency-agent.md",
    "semantic-conflict-agent": "semantic-conflict-agent.md",
    "reviewer": "reviewer.md",
    "ci-repair-agent": "ci-repair-agent.md",
    "orchestrator": "orchestrator.md",
    "eval-runner": "eval-runner.md",
    "report-agent": "report-agent.md",
}


def make_run_id(workflow: str) -> str:
    return datetime.now(timezone.utc).strftime(f"%Y%m%dT%H%M%S.%fZ-{workflow}")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_adapter(role: str, prompt: str, state: dict[str, Any]) -> dict[str, Any]:
    command = os.environ.get("AGENT_LLM_COMMAND", "")
    if not command:
        return {
            "adapter": "deterministic",
            "summary": f"{role} checkpoint recorded.",
            "next_action": "continue",
        }
    payload = json.dumps({"role": role, "prompt": prompt, "state": state})
    completed = subprocess.run(
        shlex.split(command),
        input=payload,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        return {
            "adapter": "command",
            "summary": completed.stderr.strip() or completed.stdout.strip(),
            "next_action": "blocked",
        }
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        parsed = {"summary": completed.stdout.strip(), "next_action": "continue"}
    parsed["adapter"] = "command"
    return parsed


def role_prompt(role: str) -> str:
    prompt_file = PROMPT_FILES.get(role)
    if not prompt_file:
        return ""
    path = PROMPTS / prompt_file
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run_roles(
    workflow: str = "full_agent_workflow",
    run_id: str = "",
    artifacts_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_id = run_id or make_run_id(workflow)
    run_dir = RUNS / run_id
    run_artifacts = artifacts_dir or run_dir / "artifacts"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_artifacts.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {
        "run_id": run_id,
        "workflow": workflow,
        "dry_run": dry_run,
        "execution_status": "running",
        "roles": [],
        "artifacts_dir": str(run_artifacts.resolve()),
    }
    for role in ROLE_CHAIN:
        prompt = role_prompt(role)
        output = run_adapter(role, prompt, state)
        checkpoint = {
            "time": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "prompt_file": PROMPT_FILES.get(role, ""),
            "output": output,
        }
        state["roles"].append(checkpoint)
        write_json(run_artifacts / f"{role}.json", checkpoint)
        with (run_dir / "workflow_trace.jsonl").open("a", encoding="utf-8") as trace:
            trace.write(json.dumps({"event": "role_completed", **checkpoint}, ensure_ascii=False) + "\n")
        if output.get("next_action") == "blocked":
            state["execution_status"] = "blocked"
            write_json(run_dir / "agent_workflow.json", state)
            return state
    state["execution_status"] = "completed"
    write_json(run_dir / "agent_workflow.json", state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default="full_agent_workflow")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = run_roles(args.workflow, args.run_id, args.artifacts_dir, args.dry_run)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0 if state["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
