#!/usr/bin/env python3
"""Small profile-aware security scanner for agent-managed repositories."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PROFILE_ARTIFACT = ROOT / "artifacts" / "project_profile.json"
EXCLUDED_DIRS = {
    ".git",
    ".idea",
    ".pytest_cache",
    "__pycache__",
}
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bghu_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bghs_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bANTHROPIC_API_KEY\s*=\s*[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bOPENAI_API_KEY\s*=\s*[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:password|token|credential|secret)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
]
PRIVATE_PATH_PATTERN = re.compile(r"/Users/user/(?!agents\b)[^\s\"']+")
TARGET_REPOSITORY_PROTECTED_PREFIXES = (
    ".agents/",
    "artifacts/",
    "docs/projects/",
    "external/agents/",
)


def load_default_profile() -> str:
    if not PROJECT_PROFILE_ARTIFACT.exists():
        return "agent_workspace"
    try:
        data = json.loads(PROJECT_PROFILE_ARTIFACT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "agent_workspace"
    profile = data.get("project_profile")
    if profile in {"agent_workspace", "django", "flowfox"}:
        return profile
    return "agent_workspace"


def iter_files(repo: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(repo).parts):
            continue
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
            continue
        files.append(path)
    return files


def staged_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def protected_staged_prefixes(profile: str) -> tuple[str, ...]:
    if profile == "agent_workspace":
        return ()
    return TARGET_REPOSITORY_PROTECTED_PREFIXES


def scan(
    repo: Path = ROOT,
    profile: str | None = None,
    staged_paths: list[str] | None = None,
) -> list[str]:
    repo = repo.resolve()
    active_profile = profile or load_default_profile()
    findings: list[str] = []
    protected_prefixes = protected_staged_prefixes(active_profile)
    for relative in staged_paths if staged_paths is not None else staged_files(repo):
        if relative.startswith(protected_prefixes):
            findings.append(f"staged protected/private path: {relative}")
    for path in iter_files(repo):
        relative = path.relative_to(repo)
        if relative.name.startswith(".env"):
            findings.append(f"environment file present: {relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"possible secret in {relative}")
                break
        if str(relative) != "scripts/security_scan.py" and PRIVATE_PATH_PATTERN.search(text):
            findings.append(f"private absolute path in {relative}")
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument(
        "--profile",
        choices=("agent_workspace", "django", "flowfox"),
        default=None,
        help="Project profile. Defaults to artifacts/project_profile.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    findings = scan(repo=args.repo, profile=args.profile)
    if findings:
        for finding in findings:
            print(f"security: {finding}")
        return 1
    print("security: no obvious secrets, private keys, private paths, or protected staged files found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
