from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from ai_harness.attachments import ABSOLUTE_MAX_FILE_BYTES, MAX_ATTACHMENTS
from ai_harness import cli
from ai_harness.project import (
    CONFIG_RELATIVE_PATH,
    ProjectConfigError,
    config_fingerprint,
    load_project_config,
    project_attachment_limits,
    project_is_trusted,
    register_local_project,
)


MIB = 1024 * 1024


def project_document(*, attachments: dict[str, object] | None = None) -> dict[str, object]:
    document: dict[str, object] = {
        "version": 1,
        "project": {
            "id": "sample",
            "profile": "agent_workspace",
            "repository": ".",
            "base_branch": "main",
            "branch_prefix": "feat/",
        },
        "runtime": {"provider": "codex-sdk"},
    }
    if attachments is not None:
        document["attachments"] = attachments
    return document


def write_project(repository: Path, document: dict[str, object]) -> None:
    path = repository / CONFIG_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def test_legacy_project_config_preserves_document_shape_and_fingerprint(
    tmp_path: Path,
) -> None:
    document = project_document()
    write_project(tmp_path, document)

    config = load_project_config(tmp_path)
    expected_payload = yaml.safe_dump(document, sort_keys=True, allow_unicode=True)

    assert config.attachments is None
    assert config.as_document() == document
    assert config_fingerprint(config) == hashlib.sha256(
        expected_payload.encode("utf-8")
    ).hexdigest()
    limits = project_attachment_limits(config)
    assert limits.max_files == 5
    assert limits.max_file_bytes == 100 * MIB
    assert limits.max_task_bytes == 500 * MIB


def test_optional_attachment_limits_are_fingerprinted_and_locally_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "config"))
    write_project(
        repository,
        project_document(
            attachments={
                "max_files": 5,
                "max_file_bytes": ABSOLUTE_MAX_FILE_BYTES,
                "max_task_bytes": 2 * 1024 * MIB,
            }
        ),
    )

    config = load_project_config(repository)
    limits = project_attachment_limits(config)

    assert limits.max_files == MAX_ATTACHMENTS == 5
    assert limits.max_file_bytes == 512 * MIB
    assert limits.max_task_bytes == 2 * 1024 * MIB
    assert project_is_trusted(config) is False
    register_local_project(config)
    assert project_is_trusted(config) is True

    changed = project_document(
        attachments={
            "max_files": 5,
            "max_file_bytes": 511 * MIB,
            "max_task_bytes": 2 * 1024 * MIB,
        }
    )
    write_project(repository, changed)
    assert project_is_trusted(load_project_config(repository)) is False


def test_raising_only_file_limit_raises_effective_task_limit_to_match(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        project_document(
            attachments={"max_file_bytes": ABSOLUTE_MAX_FILE_BYTES}
        ),
    )

    limits = project_attachment_limits(load_project_config(tmp_path))

    assert limits.max_file_bytes == 512 * MIB
    assert limits.max_task_bytes == 512 * MIB


def test_force_updating_project_config_preserves_attachment_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "trust"))
    document = project_document(
        attachments={
            "max_files": 5,
            "max_file_bytes": 256 * MIB,
            "max_task_bytes": 1024 * MIB,
        }
    )
    document["runtime"] = {"provider": "codex-cli"}
    write_project(repository, document)

    assert (
        cli.main(
            [
                "init",
                "--repo",
                str(repository),
                "--force",
                "--base-branch",
                "release",
            ]
        )
        == 0
    )
    capsys.readouterr()
    config = load_project_config(repository)

    assert config.base_branch == "release"
    assert config.runtime_provider == "codex-cli"
    assert project_attachment_limits(config).max_file_bytes == 256 * MIB
    assert project_attachment_limits(config).max_task_bytes == 1024 * MIB
    assert project_is_trusted(config) is True


@pytest.mark.parametrize(
    "attachments",
    [
        {"max_files": 6},
        {"max_files": 0},
        {"max_file_bytes": ABSOLUTE_MAX_FILE_BYTES + 1},
        {"max_file_bytes": 0},
        {"max_task_bytes": MAX_ATTACHMENTS * ABSOLUTE_MAX_FILE_BYTES + 1},
        {"max_task_bytes": 0},
        {"max_file_bytes": 2, "max_task_bytes": 1},
        {"max_files": True},
        {"unknown": 1},
        None,
    ],
)
def test_invalid_attachment_limits_are_rejected(
    tmp_path: Path, attachments: dict[str, object] | None
) -> None:
    document = project_document(attachments=attachments)
    if attachments is None:
        document["attachments"] = None
    write_project(tmp_path, document)

    with pytest.raises(ProjectConfigError, match="attachments"):
        load_project_config(tmp_path)
