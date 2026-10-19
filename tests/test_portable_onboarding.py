from __future__ import annotations

import errno
import json
import socket
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from ai_harness import cli
from ai_harness.project import detect_base_branch, load_project_config


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def repo(root: Path, branch: str = "develop", *, commit: bool = True) -> Path:
    root.mkdir()
    git(root, "init", "-b", branch)
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    if commit:
        (root / "README.md").write_text("project\n")
        git(root, "add", "README.md")
        git(root, "commit", "-m", "initial")
    return root


@pytest.fixture(autouse=True)
def trust_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "config"))


@pytest.mark.parametrize("branch", ["master", "trunk", "develop", "team/custom"])
@pytest.mark.parametrize(
    "marker,profile",
    [
        ("manage.py", "django"),
        ("package.json", "nextjs_web"),
        ("pyproject.toml", "agent_workspace"),
    ],
)
def test_init_detects_real_base_across_project_profiles(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    branch: str,
    marker: str,
    profile: str,
) -> None:
    repository = repo(tmp_path / "target", branch)
    (repository / marker).write_text("{}\n")
    git(
        repository,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/missing",
    )
    assert cli.main(["init", "--repo", str(repository), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["base_branch"] == branch
    assert result["profile"] == profile
    assert cli.available_base_ref(repository, branch) == branch


def test_detached_head_uses_an_existing_local_branch(tmp_path: Path) -> None:
    repository = repo(tmp_path / "target")
    git(repository, "checkout", "--detach")
    assert detect_base_branch(repository) == "develop"


def test_init_repairs_missing_legacy_main_without_replacing_project_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = repo(tmp_path / "target", "master")
    assert (
        cli.main(
            [
                "init",
                "--repo",
                str(repository),
                "--base-branch",
                "main",
                "--branch-prefix",
                "fix/",
            ]
        )
        == 0
    )
    capsys.readouterr()
    agents = repository / "AGENTS.md"
    agents.write_text("# Keep my custom rules\n")
    assert cli.main(["init", "--repo", str(repository), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["base_branch"] == "master"
    assert load_project_config(repository).branch_prefix == "fix/"
    assert agents.read_text() == "# Keep my custom rules\n"


@pytest.mark.parametrize("unborn", [False, True])
@pytest.mark.parametrize(
    "provider,probe_ok",
    [("codex-sdk", True), ("codex-sdk", False), ("codex-cli", False)],
)
def test_full_doctor_checks_the_selected_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unborn: bool,
    provider: str,
    probe_ok: bool,
) -> None:
    repository = repo(tmp_path / "target", "master", commit=not unborn)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    if provider == "codex-cli":
        from dataclasses import replace

        from ai_harness.project import write_project_config

        write_project_config(
            replace(load_project_config(repository), runtime_provider=provider),
            force=True,
        )
        assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(cli, "harness_home", lambda: root)
    monkeypatch.setattr(cli, "configured_codex_command", lambda: "")
    monkeypatch.setattr(cli, "missing_runtime_imports", list)
    monkeypatch.setattr(cli, "verify_managed_sdk_session", lambda _: "ready")
    monkeypatch.setattr(cli, "source_build_comparison", lambda *_: (None, True))
    monkeypatch.setattr(
        cli,
        "worker_service_status",
        lambda _: {
            "alive": True,
            "stale_build": False,
            "pid": 123,
            "last_error": {},
            "build_fingerprint": "test",
        },
    )
    original_run = subprocess.run
    probes = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if any(str(value).endswith("check_runtime.py") for value in args):
            probes.append(args)
            return subprocess.CompletedProcess(
                args,
                0 if probe_ok else 1,
                json.dumps(
                    {
                        "runtime": {"provider": provider},
                        "blockers": [] if probe_ok else ["subscription unavailable"],
                    }
                ),
                "",
            )
        return original_run(args, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli.main(["doctor", "--full", "--repo", str(repository), "--json"]) == (
        0 if probe_ok else 1
    )
    result = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in result["checks"]}
    if provider == "codex-cli":
        assert checks["codex_cli"]["status"] == "fail"
        assert not probes
        return
    assert checks["codex_cli"]["status"] == "pass"
    if not probe_ok:
        assert checks["runtime_preflight"]["status"] == "fail"
        assert "subscription unavailable" in checks["runtime_preflight"]["detail"]
        assert "managed_sdk_session" not in checks
        return
    assert "not required" in checks["codex_cli"]["detail"]
    assert checks["runtime_preflight"]["status"] == "pass"
    assert checks["managed_sdk_session"]["status"] == "pass"
    assert probes[0][-2:] == ["--runtime-provider", "codex-sdk"]
    assert checks["base_branch"]["status"] == ("warn" if unborn else "pass")
    if unborn:
        assert "first project commit" in checks["base_branch"]["detail"]
        assert git(repository, "status", "--porcelain") == ""


def test_dashboard_falls_back_atomically_from_an_occupied_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = repo(tmp_path / "target")
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    busy_port = occupied.getsockname()[1]
    attempted, opened = [], []

    class Controller:
        @staticmethod
        def serve_control_plane(**kwargs: object) -> None:
            attempted.append(kwargs["port"])
            assert kwargs["host"] == "127.0.0.1"
            assert kwargs["auth_token"]
            with socket.socket() as listener:
                listener.bind((kwargs["host"], kwargs["port"]))
                kwargs["on_ready"](listener.getsockname()[1])

    monkeypatch.setattr(cli, "harness_home", lambda: tmp_path)
    monkeypatch.setattr(cli, "load_harness_module", lambda *_: Controller)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    try:
        assert (
            cli.main(["dashboard", "--repo", str(repository), "--port", str(busy_port)])
            == 0
        )
        assert attempted == [busy_port, 0]
        assert opened[0].startswith("http://127.0.0.1:")
        assert f":{busy_port}/" not in opened[0]
        assert parse_qs(urlsplit(opened[0]).fragment).get("token")
        assert "selecting a free local port" in capsys.readouterr().out
    finally:
        occupied.close()


def test_dashboard_does_not_retry_unrelated_socket_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = repo(tmp_path / "target")
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()

    class Controller:
        @staticmethod
        def serve_control_plane(**kwargs: object) -> None:
            raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(cli, "harness_home", lambda: tmp_path)
    monkeypatch.setattr(cli, "load_harness_module", lambda *_: Controller)
    assert cli.main(["dashboard", "--repo", str(repository), "--no-open"]) == 2
    assert "permission denied" in capsys.readouterr().err


@pytest.mark.parametrize("tracked_ide", [False, True])
def test_dirty_checkout_automatically_uses_worktree_without_staging_user_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tracked_ide: bool,
) -> None:
    repository = repo(tmp_path / "target")
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    idea = repository / ".idea/misc.xml"
    idea.parent.mkdir()
    idea.write_text("user IDE settings\n")
    if tracked_ide:
        git(repository, "add", ".idea/misc.xml")
        git(repository, "commit", "-m", "project IDE settings")
        idea.write_text("modified user IDE settings\n")
    before_status = git(repository, "status", "--porcelain")
    before_branch = git(repository, "branch", "--show-current")
    before_head = git(repository, "rev-parse", "HEAD")
    state_root = tmp_path / "harness-state"
    state_root.mkdir()
    source_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(source_root / "scripts"))
    (state_root / ".agent-recovery.yaml").write_bytes(
        (source_root / ".agent-recovery.yaml").read_bytes()
    )
    monkeypatch.setattr(cli, "harness_home", lambda: state_root)
    monkeypatch.setattr(cli, "missing_runtime_imports", list)
    monkeypatch.setattr(cli, "verify_managed_sdk_session", lambda _: "ready")
    monkeypatch.setattr(
        cli,
        "run_worker_command",
        lambda *_args, **_kwargs: {"status": "starting", "pid": 123},
    )
    assert (
        cli.main(
            [
                "task",
                "Review the committed project",
                "--repo",
                str(repository),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["workspace_mode"] == "worktree"
    assert git(repository, "branch", "--show-current") == before_branch
    assert git(repository, "rev-parse", "HEAD") == before_head
    assert git(repository, "status", "--porcelain") == before_status
    assert git(repository, "diff", "--cached") == ""
