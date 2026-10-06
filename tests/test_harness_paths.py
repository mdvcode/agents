from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_harness import paths
from ai_harness.build import harness_build_fingerprint


@pytest.fixture(autouse=True)
def isolated_home_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("AI_HARNESS_HOME", raising=False)


def make_home(path: Path) -> Path:
    for name in (".agent-runtime.yaml", "scripts/task_queue.py", "schemas/task_envelope.schema.json"):
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("{}\n", encoding="utf-8")
    return path.resolve()


def test_saved_home_overrides_checkout_but_explicit_environment_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = make_home(tmp_path / "saved")
    explicit = make_home(tmp_path / "explicit")

    assert paths.save_harness_home(saved) == saved
    assert paths.harness_home() == saved
    assert paths.home_config_path().stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv("AI_HARNESS_HOME", str(explicit))
    assert paths.harness_home() == explicit
    paths.home_config_path().write_text("invalid json", encoding="utf-8")
    assert paths.harness_home() == explicit


@pytest.mark.parametrize("selection", ["explicit", "saved", "malformed", "invalid_path"])
def test_invalid_selection_never_falls_back_to_another_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    home = make_home(tmp_path / "selected")
    paths.save_harness_home(home)
    if selection == "malformed":
        paths.home_config_path().write_text('{"version": 1}', encoding="utf-8")
    elif selection == "invalid_path":
        paths.home_config_path().write_text(
            json.dumps({"version": 1, "home": "\0"}), encoding="utf-8"
        )
    elif selection == "explicit":
        monkeypatch.setenv("AI_HARNESS_HOME", str(tmp_path / "missing"))
    else:
        (home / ".agent-runtime.yaml").unlink()

    with pytest.raises(paths.HarnessNotFoundError, match="agent home"):
        paths.harness_home()


def test_failed_replacement_preserves_previous_home_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = make_home(tmp_path / "original")
    replacement = make_home(tmp_path / "replacement")
    paths.save_harness_home(original)

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        paths.save_harness_home(replacement)
    assert paths.harness_home() == original
    assert list(paths.home_config_path().parent.iterdir()) == [paths.home_config_path()]


def test_project_identity_cannot_select_a_control_plane(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".agent").mkdir(parents=True)
    (project / ".agent/project.yaml").write_text("harness_home: /elsewhere\n", encoding="utf-8")

    with pytest.raises(paths.HarnessNotFoundError, match="existing control plane"):
        paths.save_harness_home(project)
    assert not paths.home_config_path().exists()


def test_explicit_package_fingerprint_detects_drift_even_under_source_home(tmp_path: Path) -> None:
    source_package = tmp_path / "ai_harness"
    installed_package = tmp_path / ".venv/site-packages/ai_harness"
    for package in (source_package, installed_package):
        package.mkdir(parents=True)
        (package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    expected = harness_build_fingerprint(tmp_path)
    assert harness_build_fingerprint(tmp_path, package_root=installed_package) == expected
    (installed_package / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert harness_build_fingerprint(tmp_path, package_root=installed_package) != expected
    assert harness_build_fingerprint(tmp_path) == expected
