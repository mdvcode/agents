"""Bounded subprocess lifecycle with process-group termination and file-backed output."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


PollCallback = Callable[[], None]
StopCallback = Callable[[], bool]


@dataclass(frozen=True)
class ManagedProcessResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    idle_timed_out: bool = False
    cancelled: bool = False
    shutdown_requested: bool = False
    output_limit_exceeded: bool = False
    artifact_limit_exceeded: bool = False
    open_file_limit_exceeded: bool = False


def _process_running(process: Any) -> bool:
    return process.poll() is None


def _descendant_pids(parent_pid: int) -> set[int]:
    """Return descendants from the local process table; fail closed to the parent group."""

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid="],
            text=True,
            capture_output=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    children: dict[int, set[int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, ppid = (int(field) for field in fields)
        except ValueError:
            continue
        children.setdefault(ppid, set()).add(pid)
    descendants: set[int] = set()
    pending = list(children.get(parent_pid, set()))
    while pending:
        pid = pending.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        pending.extend(children.get(pid, set()))
    return descendants


def _signal_process_tree(parent_pid: int, descendants: set[int], sig: signal.Signals) -> None:
    current_group = os.getpgrp() if hasattr(os, "getpgrp") else -1
    groups = {parent_pid}
    ungrouped: set[int] = set()
    for pid in descendants:
        try:
            group = os.getpgid(pid)
        except (AttributeError, OSError, ProcessLookupError):
            ungrouped.add(pid)
            continue
        if group == current_group:
            ungrouped.add(pid)
        else:
            groups.add(group)
    for group in sorted(groups, reverse=True):
        if group <= 0 or group == current_group:
            continue
        try:
            os.killpg(group, sig)
        except (OSError, ProcessLookupError):
            continue
    for pid in sorted(ungrouped, reverse=True):
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            continue


def terminate_process_group(process: Any, *, grace_seconds: float) -> None:
    """Terminate a process session, escalating after a bounded grace period."""

    if not _process_running(process):
        return
    pid = int(getattr(process, "pid", 0) or 0)
    descendants = _descendant_pids(pid) if pid > 0 else set()
    try:
        if pid > 0 and hasattr(os, "killpg"):
            _signal_process_tree(pid, descendants, signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        return

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while _process_running(process) and time.monotonic() < deadline:
        time.sleep(0.05)
    if not _process_running(process) and not descendants:
        return
    while descendants and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        if pid > 0 and hasattr(os, "killpg"):
            _signal_process_tree(pid, descendants, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        return
    try:
        process.wait(timeout=max(1.0, grace_seconds))
    except (AttributeError, subprocess.TimeoutExpired):
        return


def _combined_size(stdout_path: Path, stderr_path: Path) -> int:
    total = 0
    for path in (stdout_path, stderr_path):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _tree_size(paths: Sequence[Path]) -> int:
    """Return bounded artifact usage without following symlinks."""

    total = 0
    for root in paths:
        try:
            if root.is_symlink():
                continue
            if root.is_file():
                total += root.stat().st_size
                continue
            for directory, names, files in os.walk(root, followlinks=False):
                directory_path = Path(directory)
                names[:] = [name for name in names if not (directory_path / name).is_symlink()]
                for name in files:
                    path = directory_path / name
                    if path.is_symlink():
                        continue
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def open_file_count() -> int:
    """Count open descriptors where the host exposes a descriptor filesystem."""

    for directory in (Path("/proc/self/fd"), Path("/dev/fd")):
        try:
            return sum(1 for _ in directory.iterdir())
        except OSError:
            continue
    return 0


def _enforce_combined_limit(stdout_path: Path, stderr_path: Path, max_output_bytes: int) -> None:
    remaining = max(0, max_output_bytes)
    for path in (stdout_path, stderr_path):
        try:
            size = path.stat().st_size
            if size > remaining:
                with path.open("r+b") as handle:
                    handle.truncate(remaining)
                remaining = 0
            else:
                remaining -= size
        except OSError:
            continue


def _read_bounded(path: Path, max_bytes: int) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""


def run_managed_process(
    command: Sequence[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout_seconds: float,
    idle_timeout_seconds: float,
    shutdown_grace_seconds: float,
    max_output_bytes: int,
    artifact_paths: Sequence[Path] = (),
    max_artifact_bytes: int | None = None,
    max_open_files: int | None = None,
    open_file_counter: Callable[[], int] = open_file_count,
    poll_seconds: float = 0.1,
    on_poll: PollCallback | None = None,
    cancel_requested: StopCallback | None = None,
    shutdown_requested: StopCallback | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
) -> ManagedProcessResult:
    """Run one bounded process without unread pipes and terminate its full session."""

    if not command:
        raise ValueError("managed process command is required")
    if timeout_seconds <= 0 or idle_timeout_seconds <= 0 or shutdown_grace_seconds < 0:
        raise ValueError("managed process timeouts must be positive and grace must be non-negative")
    if max_output_bytes < 1 or poll_seconds <= 0:
        raise ValueError("managed process output and polling limits must be positive")
    if max_artifact_bytes is not None and max_artifact_bytes < 1:
        raise ValueError("managed process artifact limit must be positive")
    if max_open_files is not None and max_open_files < 4:
        raise ValueError("managed process open-file limit must be at least four")

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    last_activity = started
    previous_size = 0
    timed_out = False
    idle_timed_out = False
    cancelled = False
    stopping = False
    output_limit_exceeded = False
    artifact_limit_exceeded = False
    open_file_limit_exceeded = False

    current_open_files = open_file_counter() if max_open_files is not None else 0
    if max_open_files is not None and current_open_files + 3 > max_open_files:
        return ManagedProcessResult(
            returncode=72,
            stdout="",
            stderr="managed process open-file limit exceeded",
            duration_seconds=0.0,
            open_file_limit_exceeded=True,
        )

    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        process = popen_factory(
            list(command),
            cwd=cwd,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            env=env,
            start_new_session=True,
        )
        if input_text is not None:
            encoded = input_text.encode("utf-8")
            stdin = getattr(process, "stdin", None)
            if stdin is None:
                terminate_process_group(process, grace_seconds=shutdown_grace_seconds)
                raise RuntimeError("managed process stdin is unavailable")
            try:
                stdin.write(encoded)
                stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                stdin.close()

        try:
            while _process_running(process):
                now = time.monotonic()
                size = _combined_size(stdout_path, stderr_path)
                if size != previous_size:
                    previous_size = size
                    last_activity = now
                if size > max_output_bytes:
                    output_limit_exceeded = True
                    terminate_process_group(process, grace_seconds=shutdown_grace_seconds)
                    break
                if max_artifact_bytes is not None and _tree_size(artifact_paths) > max_artifact_bytes:
                    artifact_limit_exceeded = True
                    terminate_process_group(process, grace_seconds=shutdown_grace_seconds)
                    break
                if cancel_requested is not None and cancel_requested():
                    cancelled = True
                    terminate_process_group(process, grace_seconds=shutdown_grace_seconds)
                    break
                if shutdown_requested is not None and shutdown_requested():
                    stopping = True
                    terminate_process_group(process, grace_seconds=shutdown_grace_seconds)
                    break
                if now - started >= timeout_seconds:
                    timed_out = True
                    terminate_process_group(process, grace_seconds=shutdown_grace_seconds)
                    break
                if now - last_activity >= idle_timeout_seconds:
                    idle_timed_out = True
                    terminate_process_group(process, grace_seconds=shutdown_grace_seconds)
                    break
                if on_poll is not None:
                    on_poll()
                time.sleep(poll_seconds)
        except Exception:
            terminate_process_group(process, grace_seconds=shutdown_grace_seconds)
            raise

    if _combined_size(stdout_path, stderr_path) > max_output_bytes:
        output_limit_exceeded = True
    if max_artifact_bytes is not None and _tree_size(artifact_paths) > max_artifact_bytes:
        artifact_limit_exceeded = True
    _enforce_combined_limit(stdout_path, stderr_path, max_output_bytes)
    returncode = int(getattr(process, "returncode", 1) or 0)
    if timed_out or idle_timed_out:
        returncode = 124
    elif cancelled:
        returncode = 130
    elif stopping:
        returncode = 75
    elif output_limit_exceeded:
        returncode = 74
    elif artifact_limit_exceeded:
        returncode = 73
    return ManagedProcessResult(
        returncode=returncode,
        stdout=_read_bounded(stdout_path, max_output_bytes),
        stderr=_read_bounded(stderr_path, max_output_bytes),
        duration_seconds=max(0.0, time.monotonic() - started),
        timed_out=timed_out,
        idle_timed_out=idle_timed_out,
        cancelled=cancelled,
        shutdown_requested=stopping,
        output_limit_exceeded=output_limit_exceeded,
        artifact_limit_exceeded=artifact_limit_exceeded,
        open_file_limit_exceeded=open_file_limit_exceeded,
    )
