#!/usr/bin/env python3
"""Worker-owned persistent Codex SDK session over a local Unix socket."""

from __future__ import annotations

import argparse
import json
import os
import signal
import select
import socket
import sys
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
for candidate in (SCRIPT_DIR, SCRIPT_DIR.parent, ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from codex_sdk_executor import (  # noqa: E402
    MAX_SESSION_MESSAGE_BYTES,
    atomic_write_json,
    run_sdk,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CodexSdkServer:
    def __init__(
        self,
        *,
        socket_path: Path,
        state_path: Path,
        max_requests: int,
        max_age_seconds: int,
    ) -> None:
        self.socket_path = socket_path.resolve()
        self.state_path = state_path.resolve()
        self.max_requests = max_requests
        self.max_age_seconds = max_age_seconds
        self.started_monotonic = time.monotonic()
        self.started_at = utc_now()
        self.stop_requested = False
        self.codex: Any | None = None
        self.server: socket.socket | None = None
        existing = self._read_state()
        threads = existing.get("threads", {})
        self.threads = dict(threads) if isinstance(threads, dict) else {}
        self.request_count = 0
        previous_restart_count = existing.get("restart_count", -1)
        self.restart_count = (
            int(previous_restart_count) + 1
            if isinstance(previous_restart_count, int)
            else 0
        )

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def state(self) -> dict[str, Any]:
        return self._read_state()

    def write_state(
        self, status: str, *, active_run_id: str = "", stop_reason: str = ""
    ) -> None:
        atomic_write_json(
            self.state_path,
            {
                "pid": os.getpid(),
                "status": status,
                "socket_path": str(self.socket_path),
                "started_at": self.started_at,
                "heartbeat_at": utc_now(),
                "age_seconds": round(time.monotonic() - self.started_monotonic, 3),
                "request_count": self.request_count,
                "restart_count": self.restart_count,
                "active_run_id": active_run_id,
                "stop_reason": stop_reason,
                "threads": self.threads,
            },
        )

    def ensure_codex(self, request: dict[str, Any]) -> Any:
        if self.codex is not None:
            return self.codex
        from openai_codex import Codex, CodexConfig

        repository = str(Path(str(request["repository"])).resolve())
        environment = {
            "AGENT_TOOL_POLICY_PATH": str(ROOT / ".agent-tool-policy.yaml"),
            "AGENT_CODEX_MANAGED_SESSION": "1",
        }
        writable_roots = [str((ROOT / ".agent-runs").resolve())]
        self.codex = Codex(
            CodexConfig(
                cwd=repository,
                env=environment,
                config_overrides=(
                    "features.fast_mode=true",
                    "sandbox_workspace_write.writable_roots="
                    + json.dumps(writable_roots),
                ),
                client_name="ai_harness_worker",
                client_title="AI Harness Worker",
            )
        )
        return self.codex

    @staticmethod
    def send(connection: socket.socket, payload: dict[str, Any]) -> None:
        connection.sendall(
            json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        )

    def execute(self, connection: socket.socket, message: dict[str, Any]) -> None:
        request = message.get("request")
        prompt = message.get("prompt")
        output_contract = message.get("output_contract")
        manifest = message.get("manifest")
        if not isinstance(request, dict) or not isinstance(prompt, str):
            raise ValueError("session execute request is malformed")
        if not isinstance(output_contract, dict) or not isinstance(manifest, dict):
            raise ValueError("session execute contracts are malformed")
        run_id = str(request.get("run_id", ""))
        self.request_count += 1
        self.write_state("busy", active_run_id=run_id)
        monitor_stop = threading.Event()

        def progress_sink(progress: dict[str, Any]) -> None:
            live_thread_id = str(progress.get("thread_id", ""))
            if run_id and live_thread_id:
                self.threads[run_id] = live_thread_id
            self.write_state("busy", active_run_id=run_id)
            self.send(connection, {"type": "progress", "progress": progress})

        def turn_started(handle: Any) -> None:
            def monitor_client() -> None:
                while not monitor_stop.wait(0.25):
                    try:
                        readable, _writable, _errors = select.select(
                            [connection], [], [], 0
                        )
                        if readable and connection.recv(1, socket.MSG_PEEK) == b"":
                            with suppress(Exception):
                                handle.interrupt()
                            return
                    except OSError:
                        with suppress(Exception):
                            handle.interrupt()
                        return

            threading.Thread(
                target=monitor_client,
                name=f"sdk-client-{run_id[:24]}",
                daemon=True,
            ).start()

        try:
            result = run_sdk(
                request=request,
                prompt=prompt,
                output_contract=output_contract,
                manifest=manifest,
                codex_client=self.ensure_codex(request),
                thread_id=str(self.threads.get(run_id, "")),
                progress_sink=progress_sink,
                turn_started=turn_started,
            )
        finally:
            monitor_stop.set()
        thread_id = str(result.get("thread_id", ""))
        if run_id and thread_id:
            self.threads[run_id] = thread_id
        self.write_state("ready")
        self.send(connection, {"type": "result", "result": result})

    def handle(self, connection: socket.socket) -> None:
        buffer = b""
        while b"\n" not in buffer:
            chunk = connection.recv(64 * 1024)
            if not chunk:
                return
            buffer += chunk
            if len(buffer) > MAX_SESSION_MESSAGE_BYTES:
                raise ValueError("session request exceeded the configured limit")
        line, _remainder = buffer.split(b"\n", 1)
        message = json.loads(line.decode("utf-8"))
        if not isinstance(message, dict):
            raise ValueError("session request must be an object")
        action = message.get("action")
        if action == "ping":
            self.write_state("ready")
            self.send(connection, {"type": "pong", "pid": os.getpid()})
            return
        if action == "shutdown":
            self.stop_requested = True
            self.send(connection, {"type": "stopping"})
            return
        if action != "execute":
            raise ValueError(f"unsupported session action: {action!r}")
        self.execute(connection, message)

    def should_recycle(self) -> bool:
        return (
            self.request_count >= self.max_requests
            or time.monotonic() - self.started_monotonic >= self.max_age_seconds
        )

    def serve(self) -> int:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            self.socket_path.unlink()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.server.listen(4)
        self.server.settimeout(1.0)
        self.write_state("ready")
        while not self.stop_requested:
            if self.should_recycle():
                self.write_state("recycling", stop_reason="age_or_request_budget")
                break
            try:
                connection, _address = self.server.accept()
            except TimeoutError:
                self.write_state("ready")
                continue
            with connection:
                try:
                    self.handle(connection)
                except Exception as exc:
                    with suppress(OSError):
                        self.send(
                            connection,
                            {"type": "error", "error": f"{type(exc).__name__}: {exc}"},
                        )
                    self.write_state("degraded", stop_reason=type(exc).__name__)
        return 0

    def close(self) -> None:
        if self.codex is not None:
            with suppress(Exception):
                self.codex.close()
        if self.server is not None:
            with suppress(OSError):
                self.server.close()
        with suppress(OSError):
            self.socket_path.unlink()
        self.write_state(
            "stopped", stop_reason="shutdown" if self.stop_requested else "recycle"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-age-seconds", type=int, default=21600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_requests < 1 or args.max_age_seconds < 60:
        raise SystemExit("session recycle limits are invalid")
    server = CodexSdkServer(
        socket_path=args.socket,
        state_path=args.state,
        max_requests=args.max_requests,
        max_age_seconds=args.max_age_seconds,
    )

    def stop(_signum: int, _frame: object) -> None:
        server.stop_requested = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return server.serve()
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
