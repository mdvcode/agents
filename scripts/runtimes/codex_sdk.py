"""Official Python Codex SDK runtime over an existing ChatGPT subscription."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from ai_harness.processes import run_managed_process
from ai_harness.recovery.policy import load_recovery_policy
from runtimes.base import RuntimeDescriptor
from runtimes.subprocess_runtime import SubprocessRuntime


HARNESS_ROOT = Path(__file__).resolve().parents[2]


class CodexSdkRuntime(SubprocessRuntime):
    def __init__(
        self,
        *,
        command: str,
        timeout_seconds: int,
        raw_output_dir: Path | None,
    ) -> None:
        super().__init__(
            descriptor=RuntimeDescriptor(
                provider="codex-sdk",
                kind="runtime_adapter",
                transport="local_subscription",
                production=True,
                command=command,
                api_required=False,
                capabilities=("text", "local_image"),
            ),
            timeout_seconds=timeout_seconds,
            raw_output_dir=raw_output_dir,
        )

    def list_models(self, *, worktree: Path, timeout_seconds: int) -> dict[str, Any]:
        limits = load_recovery_policy().runtime_limits
        raw_dir = self.raw_output_dir or HARNESS_ROOT / ".agent-queue" / "preflight"
        raw_dir.mkdir(parents=True, exist_ok=True)
        timeout = max(1, min(timeout_seconds, 30))
        with tempfile.TemporaryDirectory(prefix="model-discovery-", dir=raw_dir) as directory:
            completed = run_managed_process(
                [sys.executable, str(HARNESS_ROOT / "scripts" / "check_codex_sdk_runtime.py"), "--repo", str(worktree), "--models"],
                cwd=HARNESS_ROOT,
                stdout_path=Path(directory) / "stdout.log", stderr_path=Path(directory) / "stderr.log",
                timeout_seconds=timeout, idle_timeout_seconds=timeout,
                shutdown_grace_seconds=limits.shutdown_grace_seconds,
                max_output_bytes=1_000_000, max_open_files=limits.max_open_files,
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {}
        if (
            completed.returncode == 0
            and not (completed.timed_out or completed.idle_timed_out or completed.output_limit_exceeded)
            and isinstance(payload, dict) and payload.get("status") == "available"
        ):
            return payload
        return {"status": "unavailable", "provider": "codex-sdk", "models": [], "message": "Model discovery failed or timed out; check Sign in with ChatGPT and retry"}

    def preflight(self, *, worktree: Path, timeout_seconds: int) -> dict[str, Any]:
        limits = load_recovery_policy().runtime_limits
        effective_timeout = min(timeout_seconds, 60)
        raw_dir = self.raw_output_dir or HARNESS_ROOT / ".agent-queue" / "preflight"
        completed = run_managed_process(
            [sys.executable, str(HARNESS_ROOT / "scripts" / "check_codex_sdk_runtime.py"), "--repo", str(worktree)],
            cwd=HARNESS_ROOT,
            stdout_path=raw_dir / "codex-sdk-preflight.stdout.log",
            stderr_path=raw_dir / "codex-sdk-preflight.stderr.log",
            timeout_seconds=effective_timeout,
            idle_timeout_seconds=effective_timeout,
            shutdown_grace_seconds=limits.shutdown_grace_seconds,
            max_output_bytes=min(limits.max_output_bytes, 1_000_000),
            max_open_files=limits.max_open_files,
        )
        if completed.timed_out or completed.idle_timed_out:
            return {
                "execution_status": "blocked",
                "blockers": [f"Codex SDK preflight timed out after {effective_timeout}s"],
                "warnings": [],
            }
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {
                "execution_status": "blocked",
                "blockers": [(completed.stderr or completed.stdout).strip() or "Codex SDK preflight returned invalid JSON"],
                "warnings": [],
            }
        if not isinstance(payload, dict):
            return {
                "execution_status": "blocked",
                "blockers": ["Codex SDK preflight returned a non-object result"],
                "warnings": [],
            }
        return payload
