#!/usr/bin/env python3
"""Preflight the Codex CLI runtime before running production role execution."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence


REQUIRED_EXEC_FLAGS = ("--json", "--sandbox", "--output-schema", "--output-last-message")


def configured_codex_base_command(command: str = "") -> list[str]:
    configured = command or os.environ.get("AGENT_CODEX_CLI_COMMAND", "")
    if not configured:
        app_candidates = (
            Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
            Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
        )
        configured = next((str(path) for path in app_candidates if path.is_file()), "codex")
    args = os.environ.get("AGENT_CODEX_CLI_ARGS", "")
    return shlex.split(configured) + shlex.split(args)


def with_exec_subcommand(command: Sequence[str]) -> list[str]:
    parts = list(command)
    if "exec" in parts:
        return parts
    return parts + ["exec"]


def command_available(command: Sequence[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    if Path(executable).is_absolute() or "/" in executable:
        return Path(executable).exists()
    return shutil.which(executable) is not None


def result(status: str, blockers: Sequence[str] = (), warnings: Sequence[str] = (), **extra: Any) -> dict[str, Any]:
    return {
        "execution_status": status,
        "blockers": list(blockers),
        "warnings": list(warnings),
        **extra,
    }


def run_command(command: Sequence[str], *, cwd: Path, timeout_seconds: int, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=stdin,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )


def check_codex_runtime(
    *,
    repo: Path,
    codex_command: str = "",
    sandbox: str = "read-only",
    timeout_seconds: int = 45,
    run_exec_probe: bool = True,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    repo = repo.resolve()
    if not repo.exists() or not repo.is_dir():
        blockers.append(f"target repo is not accessible: {repo}")

    base_command = configured_codex_base_command(codex_command)
    if not command_available(base_command):
        blockers.append("Codex CLI is not available or not authenticated.")
        return result(
            "blocked",
            blockers,
            warnings,
            command=" ".join(shlex.quote(part) for part in base_command),
            repo=str(repo),
            sandbox=sandbox,
        )

    exec_command = with_exec_subcommand(base_command)
    try:
        help_result = run_command(exec_command + ["--help"], cwd=repo if repo.exists() else Path.cwd(), timeout_seconds=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result("blocked", [f"Codex CLI help probe failed: {exc}"], warnings)

    if help_result.returncode != 0:
        output_text = (help_result.stderr or help_result.stdout).strip()
        return result(
            "blocked",
            ["Codex CLI is not available or not authenticated.", output_text or f"exit {help_result.returncode}"],
            warnings,
            command=" ".join(shlex.quote(part) for part in base_command),
            repo=str(repo),
            sandbox=sandbox,
        )

    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    missing_flags = [flag for flag in REQUIRED_EXEC_FLAGS if flag not in help_text]
    if missing_flags:
        blockers.append(f"Codex CLI exec does not advertise required flags: {', '.join(missing_flags)}")

    if blockers or not run_exec_probe:
        return result(
            "blocked" if blockers else "completed",
            blockers,
            warnings,
            command=" ".join(shlex.quote(part) for part in base_command),
            repo=str(repo),
            sandbox=sandbox,
        )

    with tempfile.TemporaryDirectory(prefix="codex-runtime-") as tmp:
        tmp_path = Path(tmp)
        schema = tmp_path / "role-result.schema.json"
        output = tmp_path / "last-message.json"
        schema.write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                    "required": ["status", "summary"],
                    "additionalProperties": False,
                }
            ),
            encoding="utf-8",
        )
        probe_command = exec_command + [
            "--json",
            "--sandbox",
            sandbox,
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(output),
            "-",
        ]
        prompt = 'Return exactly this JSON object: {"status":"ok","summary":"runtime preflight"}'
        try:
            probe = run_command(probe_command, cwd=repo, timeout_seconds=timeout_seconds, stdin=prompt)
        except subprocess.TimeoutExpired:
            return result("blocked", [f"Codex CLI exec probe timed out after {timeout_seconds}s"], warnings)
        except OSError as exc:
            return result("blocked", [f"Codex CLI exec probe failed: {exc}"], warnings)
        if probe.returncode != 0:
            output_text = "\n".join(part.strip() for part in (probe.stdout, probe.stderr) if part.strip())
            return result(
                "blocked",
                ["Codex CLI is not available or not authenticated.", output_text or f"exit {probe.returncode}"],
                warnings,
                command=" ".join(shlex.quote(part) for part in base_command),
                repo=str(repo),
                sandbox=sandbox,
            )
        if not output.exists():
            warnings.append("Codex CLI exec probe completed but did not write --output-last-message.")

    return result(
        "completed",
        blockers,
        warnings,
        command=" ".join(shlex.quote(part) for part in base_command),
        repo=str(repo),
        sandbox=sandbox,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--codex-command", default="")
    parser.add_argument("--sandbox", default="read-only")
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--skip-exec-probe", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = check_codex_runtime(
        repo=args.repo,
        codex_command=args.codex_command,
        sandbox=args.sandbox,
        timeout_seconds=args.timeout_seconds,
        run_exec_probe=not args.skip_exec_probe,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
