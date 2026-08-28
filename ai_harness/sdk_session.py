"""Lifecycle manager for one persistent Codex app-server per queue worker."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from ai_harness.processes import terminate_process_group


class SdkSessionUnavailable(RuntimeError):
    """Raised when a managed SDK sidecar cannot become healthy."""


MAX_UNIX_SOCKET_PATH_BYTES = 100


def managed_socket_root(harness_root: Path) -> Path:
    """Return a short private path that stays below macOS AF_UNIX limits."""

    harness_digest = hashlib.sha256(str(harness_root.resolve()).encode("utf-8")).hexdigest()[:10]
    return Path("/tmp") / f"ai-harness-{os.getuid()}-{harness_digest}"


class ManagedCodexSdkSession:
    def __init__(
        self,
        *,
        worker_id: str,
        harness_root: Path,
        state_root: Path,
        socket_root: Path | None = None,
        startup_timeout_seconds: float = 15.0,
        shutdown_grace_seconds: float = 3.0,
        busy_stale_seconds: float = 180.0,
    ) -> None:
        digest = hashlib.sha256(worker_id.encode("utf-8")).hexdigest()[:12]
        self.worker_id = worker_id
        self.harness_root = harness_root.resolve()
        self.state_root = state_root.resolve()
        self.socket_root = (socket_root or managed_socket_root(self.harness_root)).resolve()
        self.socket_path = self.socket_root / f"sdk-{digest}.sock"
        self.state_path = self.state_root / f"sdk-{digest}.json"
        self.stderr_path = self.state_root / f"sdk-{digest}.stderr.log"
        if len(os.fsencode(self.socket_path)) > MAX_UNIX_SOCKET_PATH_BYTES:
            raise ValueError(
                f"managed Codex SDK socket path is too long: {self.socket_path}"
            )
        self.startup_timeout_seconds = startup_timeout_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.busy_stale_seconds = busy_stale_seconds
        self.process: subprocess.Popen[bytes] | None = None

    def state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def ping(self, *, timeout_seconds: float = 1.0) -> bool:
        if not self.running() or not self.socket_path.exists():
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout_seconds)
                client.connect(str(self.socket_path))
                client.sendall(b'{"action":"ping"}\n')
                return b'"pong"' in client.recv(4096)
        except OSError:
            return False

    def start(self) -> None:
        if self.ping():
            return
        self.close()
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.socket_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.socket_root, 0o700)
        command = [
            sys.executable,
            str(self.harness_root / "scripts" / "adapters" / "codex_sdk_server.py"),
            "--socket",
            str(self.socket_path),
            "--state",
            str(self.state_path),
        ]
        with self.stderr_path.open("ab") as stderr:
            os.chmod(self.stderr_path, 0o600)
            self.process = subprocess.Popen(
                command,
                cwd=self.harness_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                env=dict(os.environ),
                start_new_session=True,
            )
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            if self.ping(timeout_seconds=0.25):
                return
            time.sleep(0.05)
        state = self.state()
        returncode = self.process.returncode if self.process is not None else None
        self.close()
        reason = str(state.get("stop_reason", ""))
        if not reason or reason in {"recycle", "shutdown"}:
            reason = (
                f"sidecar exited with code {returncode}; inspect {self.stderr_path}"
                if returncode is not None
                else "sidecar did not answer heartbeat"
            )
        raise SdkSessionUnavailable(
            f"managed Codex SDK session failed to start: {reason}"
        )

    def ensure(self) -> None:
        state = self.state()
        recycle = state.get("status") in {"recycling", "stopped", "degraded"}
        if recycle or not self.ping():
            self.start()

    def environment(self, base: dict[str, str]) -> dict[str, str]:
        self.ensure()
        return {**base, "AGENT_CODEX_SDK_SESSION_SOCKET": str(self.socket_path)}

    def heartbeat(self) -> bool:
        if not self.running():
            return False
        state = self.state()
        if state.get("status") == "busy":
            try:
                return (
                    time.time() - self.state_path.stat().st_mtime
                    <= self.busy_stale_seconds
                )
            except OSError:
                return False
        return self.ping(timeout_seconds=0.25)

    def close(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            if self.socket_path.exists():
                with suppress(OSError):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(0.25)
                        client.connect(str(self.socket_path))
                        client.sendall(b'{"action":"shutdown"}\n')
            try:
                process.wait(timeout=self.shutdown_grace_seconds)
            except subprocess.TimeoutExpired:
                terminate_process_group(
                    process, grace_seconds=self.shutdown_grace_seconds
                )
        self.process = None
        with suppress(OSError):
            self.socket_path.unlink()
