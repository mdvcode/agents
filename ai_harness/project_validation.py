"""Resolve validation commands from trusted Harness profiles for a target repository."""

from __future__ import annotations

import copy
import shlex
import sys
from pathlib import Path
from typing import Any

import yaml


def effective_project_profile(
    control_root: Path, project_profile: str, repository: Path | None = None
) -> dict[str, Any]:
    path = control_root / ".agent-project-profiles.yaml"
    if not path.exists():
        return {}
    document = yaml.safe_load(path.read_text()) or {}
    base = document.get("profiles", {}).get(project_profile, {})
    profile = copy.deepcopy(base)
    if repository is not None and repository.resolve() != control_root.resolve():
        for name, variant in base.get("validation_variants", {}).items():
            required = variant["required_markers"]
            excluded = variant["excluded_markers"]
            if all((repository / marker).exists() for marker in required) and not any(
                (repository / marker).exists() for marker in excluded
            ):
                for field in ("quality_commands", "security_commands", "test_strategy"):
                    profile[field] = copy.deepcopy(variant[field])
                profile["validation_variant"] = name
                break
    substitutions = {
        "{python}": shlex.quote(sys.executable),
        "{security_scanner}": shlex.quote(
            str(control_root / "scripts" / "security_scan.py")
        ),
        "{repository}": shlex.quote(str(repository)) if repository is not None else ".",
    }
    for group in ("quality_commands", "security_commands"):
        commands = profile.get(group, {}).get("required", [])
        rendered = []
        for command in commands:
            for key, value in substitutions.items():
                command = command.replace(key, value)
            rendered.append(command)
        profile.setdefault(group, {})["required"] = rendered
    return profile
