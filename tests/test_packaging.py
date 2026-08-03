from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_exposes_installable_agent_command_and_runtime_resources() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert document["project"]["name"] == "ai-harness"
    assert document["project"]["requires-python"] == ">=3.11"
    assert document["project"]["scripts"]["agent"] == "ai_harness.cli:main"
    data_files = document["tool"]["setuptools"]["data-files"]
    assert "share/ai-harness/scripts" in data_files
    assert "share/ai-harness/schemas" in data_files
    assert "share/ai-harness/evals/baselines" in data_files
    assert "share/ai-harness/evals/experiments" in data_files
    assert "share/ai-harness/.agents/prompts" in data_files
    root_files = data_files["share/ai-harness"]
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
