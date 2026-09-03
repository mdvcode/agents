from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_exposes_installable_agent_command_and_runtime_resources() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert document["project"]["name"] == "ai-harness"
    assert document["project"]["requires-python"] == ">=3.11"
    assert document["project"]["scripts"]["agent"] == "ai_harness.cli:main"
    assert "opentelemetry-api>=1.44,<2" in document["project"]["dependencies"]
    assert "opentelemetry-sdk>=1.44,<2" in document["project"]["dependencies"]
    data_files = document["tool"]["setuptools"]["data-files"]
    assert "share/ai-harness/scripts" in data_files
    assert "share/ai-harness/schemas" in data_files
    assert "share/ai-harness/evals/baselines" in data_files
    assert "share/ai-harness/evals/experiments" in data_files
    assert "share/ai-harness/.agents/prompts" in data_files
    skill_paths = sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md"))
    assert skill_paths
    for skill_path in skill_paths:
        destination = f"share/ai-harness/.agents/skills/{skill_path.parent.name}"
        assert str(skill_path.relative_to(ROOT)) in data_files[destination]
    assert "docs/templates/goal.md" in data_files["share/ai-harness/docs/templates"]
    root_files = data_files["share/ai-harness"]
    assert "install.sh" in root_files
    assert ".agent-repositories.yaml" not in root_files
    assert "packaging/.agent-repositories.yaml" in root_files


def test_distribution_sources_do_not_contain_checkout_identity() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = [
        pattern
        for values in document["tool"]["setuptools"]["data-files"].values()
        for pattern in values
    ]
    packaged_files = [path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file()]
    packaged_files.extend(path for path in (ROOT / "ai_harness").glob("*.py") if path.is_file())

    assert packaged_files
    for path in packaged_files:
        contents = path.read_text(encoding="utf-8")
        assert "/Users/" not in contents, path
        assert "github.com-mdvcode" not in contents, path


def test_install_script_is_executable_valid_and_uses_no_sudo() -> None:
    installer = ROOT / "install.sh"

    assert os.access(installer, os.X_OK)
    subprocess.run(["sh", "-n", str(installer)], check=True)
    contents = installer.read_text(encoding="utf-8")
    assert "sudo" not in contents
    assert "agent update" in contents


def test_install_script_installs_download_and_prints_ordinary_next_steps(tmp_path: Path) -> None:
    source = tmp_path / "AI Harness Download"
    source.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "pipx.log"
    python = fake_bin / "python3"
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    agent = fake_bin / "agent"
    agent.write_text('#!/bin/sh\nprintf "agent 0.1.0\\n"\n', encoding="utf-8")
    agent.chmod(0o755)
    pipx = fake_bin / "pipx"
    pipx.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$PIPX_TEST_LOG\"\n"
        "if [ \"$1 $2 $3\" = \"environment --value PIPX_BIN_DIR\" ]; then\n"
        "  printf '%s\\n' \"$PIPX_TEST_BIN\"\n"
        "fi\n",
        encoding="utf-8",
    )
    pipx.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "AI_HARNESS_INSTALL_SOURCE": str(source),
            "AI_HARNESS_PYTHON": str(python),
            "AI_HARNESS_PIPX": str(pipx),
            "PIPX_TEST_LOG": str(log),
            "PIPX_TEST_BIN": str(fake_bin),
        }
    )

    completed = subprocess.run(
        ["sh", str(ROOT / "install.sh")],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"install --force --python {python} {source}" in log.read_text(encoding="utf-8")
    assert "Installed: agent 0.1.0" in completed.stdout
    assert "agent init" in completed.stdout
    assert "agent doctor --full" in completed.stdout
    assert "leave them local; do not force-add them" in completed.stdout
    assert "git add -f" not in completed.stdout
    assert 'agent task "Describe what to do"' in completed.stdout
    assert "Update later with: agent update" in completed.stdout
