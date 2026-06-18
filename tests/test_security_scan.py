from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "security_scan.py"
SPEC = importlib.util.spec_from_file_location("security_scan", MODULE_PATH)
assert SPEC is not None
security_scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(security_scan)


def test_agent_workspace_allows_private_workspace_artifacts(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "risk.json").write_text("{}", encoding="utf-8")
    memory = tmp_path / "docs" / "projects" / "flowfox" / "memory"
    memory.mkdir(parents=True)
    (memory / "note.md").write_text("private workspace note", encoding="utf-8")

    findings = security_scan.scan(
        repo=tmp_path,
        profile="agent_workspace",
        staged_paths=["artifacts/risk.json", "docs/projects/flowfox/memory/note.md"],
    )

    assert findings == []


def test_flowfox_blocks_private_workspace_artifacts(tmp_path: Path) -> None:
    findings = security_scan.scan(
        repo=tmp_path,
        profile="flowfox",
        staged_paths=["artifacts/risk.json", "docs/projects/flowfox/wiki/private.md"],
    )

    assert "staged protected/private path: artifacts/risk.json" in findings
    assert "staged protected/private path: docs/projects/flowfox/wiki/private.md" in findings


def test_secret_detection_blocks_token(tmp_path: Path) -> None:
    token = "ghp_" + ("A" * 24)
    (tmp_path / "config.txt").write_text("token='" + token + "'\n", encoding="utf-8")

    findings = security_scan.scan(repo=tmp_path, profile="agent_workspace", staged_paths=[])

    assert any("possible secret in config.txt" in finding for finding in findings)


def test_env_file_blocks_any_profile(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("DEBUG=1\n", encoding="utf-8")

    findings = security_scan.scan(repo=tmp_path, profile="agent_workspace", staged_paths=[])

    assert "environment file present: .env.local" in findings
