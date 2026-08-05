from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from ai_harness.processes import ManagedProcessResult, run_managed_process


def run_process(
    tmp_path: Path,
    code: str,
    *,
    timeout: float = 5,
    idle_timeout: float = 5,
    max_output_bytes: int = 64 * 1024,
    artifact_paths: tuple[Path, ...] = (),
    max_artifact_bytes: int | None = None,
    max_open_files: int | None = None,
    open_file_counter: Callable[[], int] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    shutdown_requested: Callable[[], bool] | None = None,
) -> ManagedProcessResult:
    kwargs: dict[str, object] = {}
    if open_file_counter is not None:
        kwargs["open_file_counter"] = open_file_counter
    return run_managed_process(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        timeout_seconds=timeout,
        idle_timeout_seconds=idle_timeout,
        shutdown_grace_seconds=0.2,
        max_output_bytes=max_output_bytes,
        artifact_paths=artifact_paths,
        max_artifact_bytes=max_artifact_bytes,
        max_open_files=max_open_files,
        poll_seconds=0.01,
        cancel_requested=cancel_requested,
        shutdown_requested=shutdown_requested,
        **kwargs,
    )


def test_large_output_is_file_backed_bounded_and_never_deadlocks(tmp_path: Path) -> None:
    result = run_process(
        tmp_path,
        "import sys; sys.stdout.write('x' * 2000000); sys.stdout.flush()",
        max_output_bytes=32 * 1024,
    )

    assert result.output_limit_exceeded is True
    assert result.returncode == 74
    assert (tmp_path / "stdout.log").stat().st_size <= 32 * 1024
    assert (tmp_path / "stderr.log").stat().st_size == 0


def test_artifact_growth_terminates_process_before_unbounded_disk_use(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    result = run_process(
        tmp_path,
        (
            "import time; from pathlib import Path; "
            f"Path({str(artifacts / 'large.bin')!r}).write_bytes(b'x' * 200000); "
            "time.sleep(30)"
        ),
        artifact_paths=(artifacts,),
        max_artifact_bytes=32 * 1024,
    )

    assert result.artifact_limit_exceeded is True
    assert result.returncode == 73
    assert result.duration_seconds < 2


def test_open_file_budget_blocks_subprocess_start(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    result = run_process(
        tmp_path,
        f"from pathlib import Path; Path({str(marker)!r}).write_text('started')",
        max_open_files=16,
        open_file_counter=lambda: 14,
    )

    assert result.open_file_limit_exceeded is True
    assert result.returncode == 72
    assert not marker.exists()


def test_timeout_terminates_the_managed_process(tmp_path: Path) -> None:
    result = run_process(tmp_path, "import time; time.sleep(30)", timeout=0.1)

    assert result.timed_out is True
    assert result.returncode == 124
    assert result.duration_seconds < 2


def test_sigkill_is_reported_as_a_process_failure_without_crashing_the_caller(tmp_path: Path) -> None:
    result = run_process(tmp_path, "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")

    assert result.returncode == -9
    assert result.timed_out is False
    assert result.cancelled is False


def test_cancellation_terminates_the_full_process_group(tmp_path: Path) -> None:
    cancel = threading.Event()
    marker = tmp_path / "child-survived"
    child = (
        "import time; from pathlib import Path; "
        f"time.sleep(0.4); Path({str(marker)!r}).write_text('survived')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "print('ready', flush=True); time.sleep(30)"
    )

    def request_cancel() -> bool:
        path = tmp_path / "stdout.log"
        if path.is_file() and "ready" in path.read_text(encoding="utf-8"):
            cancel.set()
        return cancel.is_set()

    result = run_process(tmp_path, parent, cancel_requested=request_cancel)
    time.sleep(0.5)

    assert result.cancelled is True
    assert result.returncode == 130
    assert not marker.exists()


def test_graceful_shutdown_has_distinct_recoverable_outcome(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    result = run_process(tmp_path, "import time; time.sleep(30)", shutdown_requested=stop.is_set)

    assert result.shutdown_requested is True
    assert result.cancelled is False
    assert result.returncode == 75


def test_managed_process_starts_in_a_distinct_process_group(tmp_path: Path) -> None:
    if not hasattr(os, "getpgid"):
        return
    result = run_process(tmp_path, "import os; print(f'{os.getpid()}:{os.getpgrp()}')")
    pid, process_group = (int(item) for item in result.stdout.strip().split(":"))

    assert pid == process_group
