"""Deterministic installed-build identity for worker freshness checks."""

from __future__ import annotations

import hashlib
from pathlib import Path


CONFIG_FILES = (
    ".agent-artifact-owners.yaml",
    ".agent-policy.yaml",
    ".agent-project-profiles.yaml",
    ".agent-role-policy.yaml",
    ".agent-recovery.yaml",
    ".agent-role-capabilities.yaml",
    ".agent-role-contracts.yaml",
    ".agent-routing.yaml",
    ".agent-runtime.yaml",
    ".agent-tool-policy.yaml",
    ".agent-workflows.yaml",
    "AGENTS.md",
    "Makefile",
)


def harness_build_fingerprint(root: Path, *, package_root: Path | None = None) -> str:
    """Hash the files that define installed worker and runtime behavior."""

    root = root.resolve()
    candidates = [root / relative for relative in CONFIG_FILES]
    for directory, pattern in (
        (
            root / "ai_harness"
            if (root / "ai_harness").is_dir()
            else (package_root or Path(__file__).resolve().parent),
            "*.py",
        ),
        (root / "scripts", "*.py"),
        (root / "schemas", "*.json"),
        (root / ".agents" / "prompts", "*.md"),
    ):
        if directory.is_dir():
            candidates.extend(directory.rglob(pattern))
    digest = hashlib.sha256()
    entries: dict[str, bytes] = {}
    for path in {item.resolve() for item in candidates if item.is_file()}:
        try:
            try:
                relative = path.relative_to(root)
            except ValueError:
                relative = Path("ai_harness") / path.relative_to(
                    package_root or Path(__file__).resolve().parent
                )
            entries[str(relative)] = path.read_bytes()
        except (OSError, ValueError):
            continue
    for relative, content in sorted(entries.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()
