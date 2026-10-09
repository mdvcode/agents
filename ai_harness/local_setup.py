"""Keep verified, generated setup private without hiding project changes."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from .project import CONFIG_RELATIVE_PATH, ProjectConfig, ProjectConfigError, project_is_trusted


AGENTS_TEMPLATE = """# AGENTS.md

## Project

This repository is initialized for the local AI Harness. Project metadata lives in `.agent/project.yaml`.

## Working rules

- Make minimal, reviewable changes in the task branch.
- Never commit directly to the default branch.
- Run the project checks selected by the Harness before review or publication.
- Do not expose secrets, private data, raw traces, or local Harness state.
- Never auto-merge or deploy without explicit human approval.
- Follow any more specific repository instructions added below this section.
"""

MAX_EXCLUDE_BYTES = 256 * 1024


def _git(repository: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments], cwd=repository, capture_output=True, text=True,
            check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectConfigError("could not inspect local setup Git metadata") from exc


def _append_local_rules(common: Path, rules: list[str]) -> None:
    """Append only; refuse redirected metadata and preserve existing bytes."""
    if common.is_symlink() or not common.is_dir():
        raise ProjectConfigError("local setup Git directory must not be a symbolic link")
    info = common / "info"
    if info.is_symlink():
        raise ProjectConfigError("local setup Git info directory must not be a symbolic link")
    try:
        info.mkdir(exist_ok=True)
        directory_fd = os.open(info, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(
                "exclude", os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                0o600, dir_fd=directory_fd,
            )
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ProjectConfigError("local setup Git exclude must be a regular private file")
                if metadata.st_size > MAX_EXCLUDE_BYTES:
                    raise ProjectConfigError("local setup Git exclude exceeds its supported size")
                existing = os.read(fd, MAX_EXCLUDE_BYTES + 1)
                if len(existing) > MAX_EXCLUDE_BYTES:
                    raise ProjectConfigError("local setup Git exclude exceeds its supported size")
                missing = [rule.encode() for rule in rules if rule.encode() not in existing.splitlines()]
                if missing:
                    prefix = b"\n" if existing and not existing.endswith(b"\n") else b""
                    block = prefix + b"\n# Local Harness-generated setup\n" + b"\n".join(missing) + b"\n"
                    while block:
                        written = os.write(fd, block)
                        if written <= 0:
                            raise ProjectConfigError("could not append local setup Git rules")
                        block = block[written:]
            finally:
                os.close(fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ProjectConfigError("could not keep generated setup local; check Git metadata access") from exc


def keep_generated_setup_local(config: ProjectConfig) -> list[str]:
    """Ignore exact untracked setup in this Git repo, only after local trust."""
    if not project_is_trusted(config):
        raise ProjectConfigError("local setup requires a trusted project configuration")
    repository = config.repository.resolve()
    top = _git(repository, ["rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        return []  # Initialization before git init remains supported.
    if Path(top.stdout.rstrip("\n")).resolve() != repository:
        return []  # Never install root rules for a registered subdirectory.
    if config.path.is_symlink() or config.path.parent.is_symlink():
        raise ProjectConfigError("local setup configuration must not be a symbolic link")
    candidates = [str(CONFIG_RELATIVE_PATH)]
    agents = repository / "AGENTS.md"
    try:
        if (not agents.is_symlink() and agents.is_file()
                and agents.stat().st_size == len(AGENTS_TEMPLATE.encode())
                and agents.read_bytes() == AGENTS_TEMPLATE.encode()):
            candidates.append("AGENTS.md")
    except OSError as exc:
        raise ProjectConfigError("could not inspect generated project instructions") from exc
    untracked: list[str] = []
    for relative in candidates:
        tracked = _git(repository, ["ls-files", "--error-unmatch", "--", relative])
        if tracked.returncode == 0:
            continue
        if tracked.returncode != 1:
            raise ProjectConfigError("could not inspect tracked local setup files")
        ignored = _git(repository, ["check-ignore", "--quiet", "--", relative])
        if ignored.returncode == 1:
            untracked.append(relative)
        elif ignored.returncode != 0:
            raise ProjectConfigError("could not inspect local setup ignore rules")
    if untracked:
        common_result = _git(repository, ["rev-parse", "--git-common-dir"])
        if common_result.returncode != 0:
            raise ProjectConfigError("could not locate local setup Git metadata")
        common = Path(common_result.stdout.rstrip("\n"))
        if not common.is_absolute():
            common = repository / common
        _append_local_rules(common, ["/" + relative for relative in untracked])
    return [
        relative for relative in candidates
        if _git(repository, ["check-ignore", "--quiet", "--", relative]).returncode == 0
    ]
