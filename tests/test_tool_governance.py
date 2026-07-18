from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tool_governance import audit_tool_call, authorize_tool_call


POLICY = Path(__file__).resolve().parents[1] / ".agent-tool-policy.yaml"


def test_github_publication_actions_are_allowlisted_and_merge_is_denied() -> None:
    create = authorize_tool_call(
        role="publication",
        tool="github",
        action="create_pr",
        domain="github.com",
        credential_type="gh_auth",
        timeout_seconds=60,
        policy_path=POLICY,
    )
    merge = authorize_tool_call(
        role="publication",
        tool="github",
        action="merge",
        domain="github.com",
        credential_type="gh_auth",
        timeout_seconds=60,
        policy_path=POLICY,
    )
    assert create.allowed is True
    assert merge.allowed is False


def test_playwright_is_loopback_only() -> None:
    local = authorize_tool_call(
        role="frontend-qa-agent",
        tool="playwright",
        action="navigate",
        domain="http://127.0.0.1:3000",
        timeout_seconds=60,
        policy_path=POLICY,
    )
    external = authorize_tool_call(
        role="frontend-qa-agent",
        tool="playwright",
        action="navigate",
        domain="https://example.com",
        timeout_seconds=60,
        policy_path=POLICY,
    )
    assert local.allowed is True
    assert external.allowed is False


def test_role_and_timeout_are_enforced_and_decision_is_audited(tmp_path: Path) -> None:
    decision = authorize_tool_call(
        role="reviewer",
        tool="shell",
        action="project_command",
        timeout_seconds=901,
        policy_path=POLICY,
    )
    audit_tool_call(tmp_path, decision, phase="test")
    entry = json.loads((tmp_path / "raw-events" / "tool-calls.jsonl").read_text(encoding="utf-8"))
    assert decision.allowed is False
    assert entry["credential_type"] == ""
    assert entry["allowed"] is False
