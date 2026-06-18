#!/usr/bin/env python3
"""Small repository-local security scanner for the agent workspace."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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
PROTECTED_STAGED_PREFIXES = (
    "artifacts/",
    "docs/projects/flowfox/issues/",
    "docs/projects/flowfox/memory/",
    "docs/projects/flowfox/wiki/",
    "docs/projects/flowfox/graph/",
)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
            continue
        files.append(path)
    return files


def staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def scan() -> list[str]:
    findings: list[str] = []
    for relative in staged_files():
        if relative.startswith(PROTECTED_STAGED_PREFIXES):
            findings.append(f"staged protected/private path: {relative}")
    for path in iter_files():
        relative = path.relative_to(ROOT)
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


def main() -> int:
    findings = scan()
    if findings:
        for finding in findings:
            print(f"security: {finding}")
        return 1
    print("security: no obvious secrets, private keys, private paths, or protected staged files found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
