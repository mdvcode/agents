#!/usr/bin/env python3
"""Authorize and audit role tool usage from a declarative policy."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / ".agent-tool-policy.yaml"
CAPABILITY_ACTIONS = {
    "filesystem_read": "read",
    "repository_search": "search",
    "filesystem_write": "write",
    "apply_patch": "patch",
    "git_diff": "diff",
    "shell": "project_command",
    "playwright": "navigate",
    "browser": "navigate",
    "git": "status",
    "github": "read_pr",
}


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    role: str
    tool: str
    action: str
    reason: str
    side_effects: str
    timeout_seconds: int
    domain: str
    credential_type: str


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def normalized_domain(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"//{value}")
    return (parsed.hostname or value).strip("[]").lower()


def authorize_tool_call(
    *,
    role: str,
    tool: str,
    action: str,
    domain: str = "",
    credential_type: str = "",
    timeout_seconds: int = 0,
    policy_path: Path = POLICY_PATH,
) -> ToolDecision:
    policy = load_policy(policy_path)
    tools = policy.get("tools", {}) if isinstance(policy, dict) else {}
    rule = tools.get(tool, {}) if isinstance(tools, dict) else {}
    maximum = int(rule.get("timeout_seconds", policy.get("defaults", {}).get("timeout_seconds", 0)) or 0) if isinstance(rule, dict) else 0
    requested_timeout = timeout_seconds or maximum
    side_effects = str(rule.get("side_effects", "unknown")) if isinstance(rule, dict) else "unknown"
    host = normalized_domain(domain)
    reason = "allowed"
    allowed = True
    if not isinstance(rule, dict) or not rule:
        allowed, reason = False, "tool is not declared in policy"
    elif role not in rule.get("roles", []):
        allowed, reason = False, "role is not allowed to use tool"
    elif action in rule.get("forbidden", []):
        allowed, reason = False, "action is explicitly forbidden"
    elif action not in rule.get("allowed", []):
        allowed, reason = False, "action is not allowlisted"
    elif maximum <= 0 or requested_timeout > maximum:
        allowed, reason = False, "timeout exceeds policy"
    elif host and host not in {str(item).lower() for item in rule.get("network_domains", [])}:
        allowed, reason = False, "network domain is outside policy scope"
    elif credential_type and credential_type not in rule.get("credentials", []):
        allowed, reason = False, "credential type is not allowlisted"
    return ToolDecision(
        allowed=allowed,
        role=role,
        tool=tool,
        action=action,
        reason=reason,
        side_effects=side_effects,
        timeout_seconds=requested_timeout,
        domain=host,
        credential_type=credential_type,
    )


def audit_tool_call(run_dir: Path, decision: ToolDecision, *, phase: str) -> None:
    path = run_dir / "raw-events" / "tool-calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        **asdict(decision),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--domain", default="")
    parser.add_argument("--credential-type", default="")
    parser.add_argument("--timeout-seconds", type=int, default=0)
    parser.add_argument("--run-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    decision = authorize_tool_call(
        role=args.role,
        tool=args.tool,
        action=args.action,
        domain=args.domain,
        credential_type=args.credential_type,
        timeout_seconds=args.timeout_seconds,
    )
    if args.run_dir is not None:
        audit_tool_call(args.run_dir, decision, phase="cli")
    print(json.dumps(asdict(decision), indent=2, ensure_ascii=False))
    return 0 if decision.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
