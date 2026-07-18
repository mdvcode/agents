#!/usr/bin/env python3
"""Preflight role-declared tool capabilities before executing a role."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml

from tool_governance import CAPABILITY_ACTIONS, POLICY_PATH, audit_tool_call, authorize_tool_call


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PROFILES = ROOT / ".agent-project-profiles.yaml"


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def first_command_token(command: str) -> str:
    return command.strip().split()[0] if command.strip() else ""


def executable_available(name: str) -> bool:
    return bool(name) and shutil.which(name) is not None


def playwright_available(repository: Path | None = None) -> bool:
    if shutil.which("playwright"):
        return True
    if repository is not None and (repository / "node_modules" / ".bin" / "playwright").is_file():
        return True
    completed = subprocess.run(
        [sys.executable, "-c", "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('playwright') else 1)"],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def profile_required_commands(project_profile: str, command_group: str) -> list[str]:
    profiles = load_yaml(PROJECT_PROFILES).get("profiles", {})
    profile = profiles.get(project_profile, {}) if isinstance(profiles, dict) else {}
    commands = profile.get(command_group, {}) if isinstance(profile, dict) else {}
    required = commands.get("required", []) if isinstance(commands, dict) else []
    return [str(command) for command in required if isinstance(command, str)]


def role_tool_preflight(
    *,
    role: str,
    allowed_tools: Sequence[str],
    project_profile: str,
    dry_run: bool = False,
    run_dir: Path | None = None,
    policy_path: Path = POLICY_PATH,
    repository: Path | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    status = "completed"
    tools = set(allowed_tools)

    for tool in sorted(tools):
        action = CAPABILITY_ACTIONS.get(tool, "use")
        decision = authorize_tool_call(
            role=role,
            tool=tool,
            action=action,
            policy_path=policy_path,
        )
        if run_dir is not None:
            audit_tool_call(run_dir, decision, phase="role-preflight")
        if not decision.allowed:
            blockers.append(f"tool policy denied {tool}/{action}: {decision.reason}")

    if "repository_search" in tools and not executable_available("rg"):
        warnings.append("repository_search requested but rg is unavailable; fallback search may be slower.")
    if "shell" in tools and not executable_available("sh"):
        blockers.append("shell capability requested but no POSIX shell is available.")
    if "git_diff" in tools and not executable_available("git"):
        blockers.append("git_diff capability requested but git is unavailable.")
    if "git" in tools and not executable_available("git"):
        blockers.append("git capability requested but git is unavailable.")

    if role == "frontend-qa-agent" and ({"browser", "playwright"} & tools):
        if os.environ.get("AGENT_BROWSER_AVAILABLE") != "1":
            warnings.append("browser capability is unavailable in this runtime.")
        if "playwright" in tools and not playwright_available(repository):
            warnings.append("playwright capability is unavailable in this runtime.")
        if warnings:
            status = "unavailable"

    if role == "quality-runner":
        for command in profile_required_commands(project_profile, "quality_commands"):
            token = first_command_token(command)
            if token and not executable_available(token):
                blockers.append(f"quality required command is unavailable: {token}")

    if role == "security-agent":
        for command in profile_required_commands(project_profile, "security_commands"):
            token = first_command_token(command)
            if token and not executable_available(token):
                blockers.append(f"security required command is unavailable: {token}")

    if role == "publication":
        if not executable_available("git"):
            blockers.append("publication requires git but git is unavailable.")
        if not executable_available("gh"):
            message = "publication requires gh but gh is unavailable."
            if dry_run:
                warnings.append(message)
            else:
                blockers.append(message)

    if blockers:
        status = "blocked"
    return {"status": status, "blockers": blockers, "warnings": warnings}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True)
    parser.add_argument("--project-profile", default="agent_workspace")
    parser.add_argument("--allowed-tool", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = role_tool_preflight(
        role=args.role,
        allowed_tools=args.allowed_tool,
        project_profile=args.project_profile,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["status"] != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
