from __future__ import annotations

import importlib.util
import subprocess
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
    memory = tmp_path / "docs" / "projects" / "nextjs_web" / "memory"
    memory.mkdir(parents=True)
    (memory / "note.md").write_text("private workspace note", encoding="utf-8")

    findings = security_scan.scan(
        repo=tmp_path,
        profile="agent_workspace",
        staged_paths=["artifacts/risk.json", "docs/projects/nextjs_web/memory/note.md"],
    )

    assert findings == []


def test_target_project_blocks_private_workspace_artifacts(tmp_path: Path) -> None:
    findings = security_scan.scan(
        repo=tmp_path,
        profile="nextjs_web",
        staged_paths=["artifacts/risk.json", "docs/projects/nextjs_web/wiki/private.md"],
    )

    assert "staged protected/private path: artifacts/risk.json" in findings
    assert "staged protected/private path: docs/projects/nextjs_web/wiki/private.md" in findings


def test_secret_detection_blocks_token(tmp_path: Path) -> None:
    token = "ghp_" + ("A" * 24)
    (tmp_path / "config.txt").write_text("token='" + token + "'\n", encoding="utf-8")

    findings = security_scan.scan(repo=tmp_path, profile="agent_workspace", full_repo=True)

    assert any("possible secret in config.txt" in finding for finding in findings)


def test_env_file_blocks_any_profile(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("DEBUG=1\n", encoding="utf-8")

    findings = security_scan.scan(repo=tmp_path, profile="agent_workspace", full_repo=True)

    assert "environment file present: .env.local" in findings


def test_env_example_is_allowed(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("DEBUG=1\n", encoding="utf-8")

    findings = security_scan.scan(repo=tmp_path, profile="agent_workspace", full_repo=True)

    assert findings == []


def test_changed_files_between_refs_scans_ci_diff(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    (tmp_path / "safe.txt").write_text("safe\n", encoding="utf-8")
    subprocess.run(["git", "add", "safe.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True, text=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    token = "ghp_" + ("A" * 24)
    (tmp_path / "safe.txt").write_text("token='" + token + "'\n", encoding="utf-8")
    subprocess.run(["git", "add", "safe.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "secret"], cwd=tmp_path, check=True, capture_output=True, text=True)

    changed = security_scan.changed_files_between_refs(tmp_path, base, "HEAD")
    findings = security_scan.scan(repo=tmp_path, profile="agent_workspace", staged_paths=changed)

    assert changed == ["safe.txt"]
    assert any("possible secret in safe.txt" in finding for finding in findings)


def test_changed_files_between_refs_raises_when_base_ref_missing(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    try:
        security_scan.changed_files_between_refs(tmp_path, "missing-base", "HEAD")
    except RuntimeError as exc:
        assert "missing-base" in str(exc) or "unknown revision" in str(exc)
    else:
        raise AssertionError("missing base ref must not return an empty changed-file list")
