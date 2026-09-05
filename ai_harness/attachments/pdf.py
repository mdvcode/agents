from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, Sequence

from .models import MIB, PDFRejected


_PYMUPDF_HELPER_MAX_STDOUT = 2 * MIB
_PYMUPDF_ADDRESS_SPACE_BYTES = 2 * 1024 * MIB
_PYMUPDF_ERROR_CODES = frozenset(
    {
        "encrypted_pdf",
        "invalid_pdf_page_count",
        "malformed_pdf",
        "pdf_page_count_exceeds_hard_limit",
        "pymupdf_unavailable",
        "pymupdf_worker_resource_limit",
    }
)


@dataclass(frozen=True, slots=True)
class PDFLimits:
    max_pages: int = 50
    max_total_pages: int = 10_000
    timeout_seconds: float = 30.0
    command_timeout_seconds: float = 10.0
    max_text_page_bytes: int = 2 * MIB
    max_rendered_page_bytes: int = 10 * MIB
    max_output_bytes: int = 50 * MIB
    max_stderr_bytes: int = 64 * 1024
    render_dpi: int = 144
    min_text_chars: int = 20
    max_image_pixels: int = 50_000_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_pages <= 500:
            raise ValueError("max_pages must be between 1 and 500")
        if not self.max_pages <= self.max_total_pages <= 100_000:
            raise ValueError("max_total_pages must bound max_pages")
        if self.timeout_seconds <= 0 or self.command_timeout_seconds <= 0:
            raise ValueError("PDF timeouts must be positive")
        if self.command_timeout_seconds > self.timeout_seconds:
            raise ValueError("command timeout cannot exceed total PDF timeout")
        if min(
            self.max_text_page_bytes,
            self.max_rendered_page_bytes,
            self.max_output_bytes,
            self.max_stderr_bytes,
        ) < 1:
            raise ValueError("PDF output limits must be positive")
        if not 72 <= self.render_dpi <= 300:
            raise ValueError("render_dpi must be between 72 and 300")
        if self.min_text_chars < 1 or self.max_image_pixels < 1:
            raise ValueError("PDF content limits must be positive")


@dataclass(frozen=True, slots=True)
class PDFToolchain:
    """Local commands used for PDF inspection, text extraction, and rendering."""

    pdfinfo: tuple[str, ...] | None
    pdftotext: tuple[str, ...] | None
    pdftoppm: tuple[str, ...] | None

    @classmethod
    def auto(cls) -> "PDFToolchain":
        def command(name: str) -> tuple[str, ...] | None:
            resolved = shutil.which(name)
            return (resolved,) if resolved else None

        return cls(
            pdfinfo=command("pdfinfo"),
            pdftotext=command("pdftotext"),
            pdftoppm=command("pdftoppm"),
        )

    @property
    def available(self) -> bool:
        return all((self.pdfinfo, self.pdftotext, self.pdftoppm))


@dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class _CommandFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _kill_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=1)


def _run_bounded(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    max_stdout: int,
    max_stderr: int,
) -> _CommandResult:
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise _CommandFailure("invalid_command")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": str(cwd),
    }
    try:
        proc = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise _CommandFailure("command_unavailable") from exc
    assert proc.stdout is not None
    assert proc.stderr is not None
    selector = selectors.DefaultSelector()
    streams = {
        proc.stdout.fileno(): ("stdout", proc.stdout, max_stdout),
        proc.stderr.fileno(): ("stderr", proc.stderr, max_stderr),
    }
    output: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    sizes = {"stdout": 0, "stderr": 0}
    try:
        for descriptor, (_, stream, _) in streams.items():
            os.set_blocking(descriptor, False)
            selector.register(stream, selectors.EVENT_READ, descriptor)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _CommandFailure("timeout")
            events = selector.select(min(remaining, 0.25))
            if not events and proc.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in tuple(selector.get_map().values())
                ]
            for key, _ in events:
                descriptor = key.data
                name, stream, limit = streams[descriptor]
                try:
                    chunk = os.read(descriptor, 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                sizes[name] += len(chunk)
                if sizes[name] > limit:
                    raise _CommandFailure("output_limit")
                output[name].append(chunk)
        remaining = max(0.01, deadline - time.monotonic())
        try:
            returncode = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise _CommandFailure("timeout") from exc
        return _CommandResult(
            returncode=returncode,
            stdout=b"".join(output["stdout"]),
            stderr=b"".join(output["stderr"]),
        )
    except BaseException:
        _kill_process(proc)
        raise
    finally:
        selector.close()
        for stream in (proc.stdout, proc.stderr):
            if not stream.closed:
                stream.close()


def _private_write(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_rendered_png(path: Path, max_pixels: int) -> tuple[int, int]:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise _CommandFailure("invalid_render_output")
    with path.open("rb") as handle:
        header = handle.read(24)
        if (
            len(header) != 24
            or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[12:16] != b"IHDR"
        ):
            raise _CommandFailure("invalid_render_output")
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
    if width < 1 or height < 1 or width * height > max_pixels:
        raise _CommandFailure("render_dimensions")
    return width, height


def _normalize_text(payload: bytes) -> str:
    try:
        value = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _CommandFailure("invalid_text_output") from exc
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    for character in value:
        if ord(character) < 32 and character not in "\n\t\f\b":
            raise _CommandFailure("invalid_text_output")
    return value


class PDFProcessor:
    """Extract text pages and render only pages without a usable text layer."""

    def __init__(
        self,
        *,
        toolchain: PDFToolchain | None = None,
        limits: PDFLimits | None = None,
        backend: str = "auto",
        pymupdf_module: Any | None = None,
    ) -> None:
        if backend not in {"auto", "pymupdf", "poppler"}:
            raise ValueError("backend must be auto, pymupdf, or poppler")
        if pymupdf_module is not None and backend != "pymupdf":
            raise ValueError(
                "pymupdf_module may only be injected with backend='pymupdf'"
            )
        self.toolchain = toolchain or PDFToolchain.auto()
        self.limits = limits or PDFLimits()
        self.backend = backend
        self._pymupdf = pymupdf_module

    def process(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        relative_output_dir: PurePosixPath,
        attachment_id: str,
        source_sha256: str,
    ) -> dict[str, Any]:
        if self.backend == "poppler":
            return self._process_poppler(
                pdf_path,
                output_dir,
                relative_output_dir=relative_output_dir,
                attachment_id=attachment_id,
                source_sha256=source_sha256,
            )
        # An explicitly injected backend is intended for unit fakes. Production
        # discovery never imports the native parser into this long-lived process.
        if self._pymupdf is not None:
            return self._process_pymupdf(
                pdf_path,
                output_dir,
                relative_output_dir=relative_output_dir,
                attachment_id=attachment_id,
                source_sha256=source_sha256,
            )
        if self._pymupdf_available():
            try:
                return self._process_pymupdf_isolated(
                    pdf_path,
                    output_dir,
                    relative_output_dir=relative_output_dir,
                    attachment_id=attachment_id,
                    source_sha256=source_sha256,
                )
            except PDFRejected as exc:
                if self.backend != "auto" or str(exc) != "pymupdf_unavailable":
                    raise
        if self.backend == "pymupdf":
            raise PDFRejected("pymupdf_unavailable")
        return self._process_poppler(
            pdf_path,
            output_dir,
            relative_output_dir=relative_output_dir,
            attachment_id=attachment_id,
            source_sha256=source_sha256,
        )

    @staticmethod
    def _pymupdf_available() -> bool:
        for module_name in ("pymupdf", "fitz"):
            try:
                if importlib.util.find_spec(module_name) is not None:
                    return True
            except (ImportError, ModuleNotFoundError, ValueError):
                continue
        return False

    @staticmethod
    def _load_pymupdf() -> Any | None:
        for module_name in ("pymupdf", "fitz"):
            try:
                return importlib.import_module(module_name)
            except (ImportError, OSError):
                continue
        return None

    def _pymupdf_helper_command(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        relative_output_dir: PurePosixPath,
        attachment_id: str,
        source_sha256: str,
    ) -> tuple[str, ...]:
        helper = Path(__file__).with_name("_pdf_worker.py")
        limits = json.dumps(
            asdict(self.limits),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            sys.executable,
            "-I",
            "-B",
            str(helper),
            "--pdf",
            str(pdf_path.absolute()),
            "--output-dir",
            str(output_dir.absolute()),
            "--relative-output-dir",
            relative_output_dir.as_posix(),
            "--attachment-id",
            attachment_id,
            "--source-sha256",
            source_sha256,
            "--limits-json",
            limits,
            "--address-space-bytes",
            str(_PYMUPDF_ADDRESS_SPACE_BYTES),
        )

    def _process_pymupdf_isolated(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        relative_output_dir: PurePosixPath,
        attachment_id: str,
        source_sha256: str,
    ) -> dict[str, Any]:
        output_dir = output_dir.absolute()
        if os.path.lexists(output_dir):
            raise PDFRejected("pymupdf_worker_invalid_output")
        if relative_output_dir.is_absolute() or ".." in relative_output_dir.parts:
            raise PDFRejected("pymupdf_worker_invalid_output")
        command = self._pymupdf_helper_command(
            pdf_path,
            output_dir,
            relative_output_dir=relative_output_dir,
            attachment_id=attachment_id,
            source_sha256=source_sha256,
        )
        try:
            try:
                completed = _run_bounded(
                    command,
                    cwd=output_dir.parent,
                    timeout=self.limits.timeout_seconds,
                    max_stdout=_PYMUPDF_HELPER_MAX_STDOUT,
                    max_stderr=self.limits.max_stderr_bytes,
                )
            except _CommandFailure as exc:
                raise PDFRejected(f"pymupdf_worker_{exc.code}") from exc

            if completed.returncode != 0 and not completed.stdout:
                raise PDFRejected("pymupdf_worker_failed")
            try:
                envelope = json.loads(completed.stdout.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PDFRejected("pymupdf_worker_invalid_output") from exc
            if not isinstance(envelope, dict):
                raise PDFRejected("pymupdf_worker_invalid_output")
            if envelope.get("ok") is False:
                error = envelope.get("error")
                if completed.returncode == 0 or error not in _PYMUPDF_ERROR_CODES:
                    raise PDFRejected("pymupdf_worker_failed")
                raise PDFRejected(str(error))
            if completed.returncode != 0:
                raise PDFRejected("pymupdf_worker_failed")
            if envelope.get("ok") is not True or set(envelope) != {"ok", "result"}:
                raise PDFRejected("pymupdf_worker_invalid_output")
            return self._validate_pymupdf_result(
                envelope["result"],
                output_dir,
                relative_output_dir=relative_output_dir,
                attachment_id=attachment_id,
                source_sha256=source_sha256,
            )
        except BaseException:
            self._discard_isolated_output(output_dir)
            raise

    def _validate_pymupdf_result(
        self,
        value: Any,
        output_dir: Path,
        *,
        relative_output_dir: PurePosixPath,
        attachment_id: str,
        source_sha256: str,
    ) -> dict[str, Any]:
        def invalid() -> NoReturn:
            raise PDFRejected("pymupdf_worker_invalid_output")

        def bounded_int(candidate: Any, minimum: int, maximum: int) -> int:
            if type(candidate) is not int or not minimum <= candidate <= maximum:
                invalid()
            return candidate

        if not isinstance(value, dict) or set(value) != {
            "status",
            "processor",
            "total_pages",
            "processed_pages",
            "pages",
            "issues",
            "output_bytes",
        }:
            invalid()
        if value["status"] not in {"complete", "partial"}:
            invalid()
        if value["processor"] != "pymupdf":
            invalid()
        total_pages = bounded_int(
            value["total_pages"], 1, self.limits.max_total_pages
        )
        pages = value["pages"]
        issues = value["issues"]
        if not isinstance(pages, list) or len(pages) > min(
            total_pages, self.limits.max_pages
        ):
            invalid()
        if not isinstance(issues, list) or len(issues) > 2 * self.limits.max_pages + 1:
            invalid()
        if bounded_int(value["processed_pages"], 0, self.limits.max_pages) != len(
            pages
        ):
            invalid()

        allowed_issue_codes = {
            "page_limit_exceeded",
            "pdf_output_limit",
            "pdf_timeout",
            "text_extraction_failed",
            "page_render_failed",
        }
        allowed_reasons = {
            "output_limit",
            "page_load_failed",
            "pymupdf_error",
            "render_dimensions",
        }
        for issue in issues:
            if (
                not isinstance(issue, dict)
                or issue.get("code") not in allowed_issue_codes
            ):
                invalid()
            code = issue["code"]
            if code in {"page_limit_exceeded", "pdf_output_limit", "pdf_timeout"}:
                if set(issue) != {"code", "unprocessed_pages"}:
                    invalid()
                bounded_int(issue["unprocessed_pages"], 1, total_pages)
            else:
                if set(issue) != {"code", "page", "reason"}:
                    invalid()
                bounded_int(issue["page"], 1, min(total_pages, self.limits.max_pages))
                if issue["reason"] not in allowed_reasons:
                    invalid()

        try:
            directory_metadata = output_dir.lstat()
        except OSError:
            invalid()
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_mode & 0o077
        ):
            invalid()

        expected_files: set[str] = set()
        total_output = 0
        successful_statuses = {"text", "rendered"}
        for expected_page, page in enumerate(pages, start=1):
            if not isinstance(page, dict) or page.get("page") != expected_page:
                invalid()
            status_value = page.get("status")
            if status_value == "failed":
                if set(page) != {"page", "status", "issue"}:
                    invalid()
                if page["issue"] not in allowed_reasons:
                    invalid()
                continue
            if status_value not in successful_statuses:
                invalid()
            required_page_keys = {"page", "status", "descriptor"}
            if status_value == "text":
                required_page_keys.add("text_chars")
            if set(page) != required_page_keys:
                invalid()

            descriptor = page["descriptor"]
            descriptor_keys = {
                "kind",
                "path",
                "media_type",
                "size",
                "sha256",
                "provenance",
            }
            suffix = ".txt" if status_value == "text" else ".png"
            if status_value == "rendered":
                descriptor_keys.update({"width", "height"})
            if not isinstance(descriptor, dict) or set(descriptor) != descriptor_keys:
                invalid()
            filename = f"page-{expected_page:04d}{suffix}"
            expected_path = (relative_output_dir / filename).as_posix()
            expected_kind = "text" if status_value == "text" else "local_image"
            expected_media_type = (
                "text/plain" if status_value == "text" else "image/png"
            )
            if (
                descriptor["path"] != expected_path
                or descriptor["kind"] != expected_kind
                or descriptor["media_type"] != expected_media_type
                or descriptor["provenance"]
                != {
                    "attachment_id": attachment_id,
                    "source_sha256": source_sha256,
                    "page": expected_page,
                }
            ):
                invalid()
            maximum_size = (
                self.limits.max_text_page_bytes
                if status_value == "text"
                else self.limits.max_rendered_page_bytes
            )
            size = bounded_int(descriptor["size"], 1, maximum_size)
            digest = descriptor["sha256"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                invalid()
            path = output_dir / filename
            try:
                metadata = path.lstat()
            except OSError:
                invalid()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_size != size
            ):
                invalid()
            try:
                actual_digest = _sha256(path)
            except OSError:
                invalid()
            if actual_digest != digest:
                invalid()
            if status_value == "text":
                try:
                    payload = path.read_bytes()
                    text = _normalize_text(payload)
                except (OSError, _CommandFailure):
                    invalid()
                if (
                    text.encode("utf-8") != payload
                    or bounded_int(page["text_chars"], 0, size) != len(text)
                    or sum(not character.isspace() for character in text)
                    < self.limits.min_text_chars
                ):
                    invalid()
            else:
                try:
                    width, height = _validate_rendered_png(
                        path, self.limits.max_image_pixels
                    )
                except (OSError, _CommandFailure):
                    invalid()
                if (
                    bounded_int(descriptor["width"], 1, self.limits.max_image_pixels)
                    != width
                    or bounded_int(
                        descriptor["height"], 1, self.limits.max_image_pixels
                    )
                    != height
                ):
                    invalid()
            expected_files.add(filename)
            total_output += size
            if total_output > self.limits.max_output_bytes:
                invalid()

        try:
            actual_files = {entry.name for entry in output_dir.iterdir()}
        except OSError:
            invalid()
        if actual_files != expected_files:
            invalid()
        if bounded_int(
            value["output_bytes"], 0, self.limits.max_output_bytes
        ) != total_output:
            invalid()
        complete = (
            len(pages) == total_pages
            and not issues
            and all(page["status"] in successful_statuses for page in pages)
        )
        if value["status"] != ("complete" if complete else "partial"):
            invalid()
        return value

    @staticmethod
    def _discard_isolated_output(output_dir: Path) -> None:
        try:
            metadata = output_dir.lstat()
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                shutil.rmtree(output_dir)
            else:
                output_dir.unlink()
        except OSError:
            pass

    def _process_poppler(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        relative_output_dir: PurePosixPath,
        attachment_id: str,
        source_sha256: str,
    ) -> dict[str, Any]:
        if not self.toolchain.available:
            raise PDFRejected("pdf_toolchain_unavailable")
        output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        deadline = time.monotonic() + self.limits.timeout_seconds
        info = self._inspect(pdf_path, output_dir, deadline)
        total_pages = info["pages"]
        if total_pages > self.limits.max_total_pages:
            raise PDFRejected("pdf_page_count_exceeds_hard_limit")

        page_limit = min(total_pages, self.limits.max_pages)
        pages: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        output_bytes = 0
        if total_pages > page_limit:
            issues.append(
                {
                    "code": "page_limit_exceeded",
                    "unprocessed_pages": total_pages - page_limit,
                }
            )

        for page_number in range(1, page_limit + 1):
            if time.monotonic() >= deadline:
                issues.append(
                    {
                        "code": "pdf_timeout",
                        "unprocessed_pages": page_limit - page_number + 1,
                    }
                )
                break
            remaining_output = self.limits.max_output_bytes - output_bytes
            if remaining_output <= 0:
                issues.append(
                    {
                        "code": "pdf_output_limit",
                        "unprocessed_pages": page_limit - page_number + 1,
                    }
                )
                break
            text_failure: str | None = None
            try:
                text = self._extract_text(
                    pdf_path,
                    output_dir,
                    page_number,
                    deadline,
                    min(remaining_output, self.limits.max_text_page_bytes),
                )
            except _CommandFailure as exc:
                text = ""
                text_failure = exc.code

            if sum(not character.isspace() for character in text) >= self.limits.min_text_chars:
                payload = text.encode("utf-8")
                filename = f"page-{page_number:04d}.txt"
                text_path = output_dir / filename
                _private_write(text_path, payload)
                output_bytes += len(payload)
                pages.append(
                    {
                        "page": page_number,
                        "status": "text",
                        "text_chars": len(text),
                        "descriptor": self._descriptor(
                            kind="text",
                            path=relative_output_dir / filename,
                            media_type="text/plain",
                            size=len(payload),
                            sha256=hashlib.sha256(payload).hexdigest(),
                            attachment_id=attachment_id,
                            source_sha256=source_sha256,
                            page_number=page_number,
                        ),
                    }
                )
                continue

            if text_failure is not None:
                issues.append(
                    {
                        "code": "text_extraction_failed",
                        "page": page_number,
                        "reason": text_failure,
                    }
                )
            try:
                rendered = self._render_page(
                    pdf_path,
                    output_dir,
                    page_number,
                    deadline,
                    min(remaining_output, self.limits.max_rendered_page_bytes),
                )
            except _CommandFailure as exc:
                issues.append(
                    {
                        "code": "page_render_failed",
                        "page": page_number,
                        "reason": exc.code,
                    }
                )
                pages.append(
                    {"page": page_number, "status": "failed", "issue": exc.code}
                )
                continue
            filename, size, width, height = rendered
            output_bytes += size
            pages.append(
                {
                    "page": page_number,
                    "status": "rendered",
                    "descriptor": self._descriptor(
                        kind="local_image",
                        path=relative_output_dir / filename,
                        media_type="image/png",
                        size=size,
                        sha256=_sha256(output_dir / filename),
                        attachment_id=attachment_id,
                        source_sha256=source_sha256,
                        page_number=page_number,
                        width=width,
                        height=height,
                    ),
                }
            )

        complete = len(pages) == total_pages and not issues and all(
            page["status"] in {"text", "rendered"} for page in pages
        )
        return {
            "status": "complete" if complete else "partial",
            "processor": "poppler",
            "total_pages": total_pages,
            "processed_pages": len(pages),
            "pages": pages,
            "issues": issues,
            "output_bytes": output_bytes,
        }

    def _process_pymupdf(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        relative_output_dir: PurePosixPath,
        attachment_id: str,
        source_sha256: str,
    ) -> dict[str, Any]:
        module = self._pymupdf
        if module is None:
            raise PDFRejected("pymupdf_unavailable")
        output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        try:
            document = module.open(str(pdf_path))
        except Exception as exc:
            raise PDFRejected("malformed_pdf") from exc
        try:
            if bool(getattr(document, "needs_pass", False)) or bool(
                getattr(document, "is_encrypted", False)
            ):
                raise PDFRejected("encrypted_pdf")
            try:
                total_pages = int(getattr(document, "page_count", len(document)))
            except (TypeError, ValueError, OverflowError) as exc:
                raise PDFRejected("invalid_pdf_page_count") from exc
            if total_pages < 1:
                raise PDFRejected("invalid_pdf_page_count")
            if total_pages > self.limits.max_total_pages:
                raise PDFRejected("pdf_page_count_exceeds_hard_limit")

            page_limit = min(total_pages, self.limits.max_pages)
            deadline = time.monotonic() + self.limits.timeout_seconds
            pages: list[dict[str, Any]] = []
            issues: list[dict[str, Any]] = []
            output_bytes = 0
            if total_pages > page_limit:
                issues.append(
                    {
                        "code": "page_limit_exceeded",
                        "unprocessed_pages": total_pages - page_limit,
                    }
                )

            for page_number in range(1, page_limit + 1):
                if time.monotonic() >= deadline:
                    issues.append(
                        {
                            "code": "pdf_timeout",
                            "unprocessed_pages": page_limit - page_number + 1,
                        }
                    )
                    break
                remaining_output = self.limits.max_output_bytes - output_bytes
                if remaining_output <= 0:
                    issues.append(
                        {
                            "code": "pdf_output_limit",
                            "unprocessed_pages": page_limit - page_number + 1,
                        }
                    )
                    break
                try:
                    page = document.load_page(page_number - 1)
                    text = page.get_text("text", sort=True)
                    if not isinstance(text, str):
                        raise TypeError("non-text PDF extraction result")
                    text = _normalize_text(text.encode("utf-8"))
                except Exception:
                    page = None
                    text = ""
                    issues.append(
                        {
                            "code": "text_extraction_failed",
                            "page": page_number,
                            "reason": "pymupdf_error",
                        }
                    )

                payload = text.encode("utf-8")
                meaningful = sum(not character.isspace() for character in text)
                if meaningful >= self.limits.min_text_chars and len(payload) <= min(
                    remaining_output, self.limits.max_text_page_bytes
                ):
                    filename = f"page-{page_number:04d}.txt"
                    _private_write(output_dir / filename, payload)
                    output_bytes += len(payload)
                    pages.append(
                        {
                            "page": page_number,
                            "status": "text",
                            "text_chars": len(text),
                            "descriptor": self._descriptor(
                                kind="text",
                                path=relative_output_dir / filename,
                                media_type="text/plain",
                                size=len(payload),
                                sha256=hashlib.sha256(payload).hexdigest(),
                                attachment_id=attachment_id,
                                source_sha256=source_sha256,
                                page_number=page_number,
                            ),
                        }
                    )
                    continue
                if meaningful >= self.limits.min_text_chars:
                    issues.append(
                        {
                            "code": "text_extraction_failed",
                            "page": page_number,
                            "reason": "output_limit",
                        }
                    )
                if page is None:
                    try:
                        page = document.load_page(page_number - 1)
                    except Exception:
                        pages.append(
                            {
                                "page": page_number,
                                "status": "failed",
                                "issue": "page_load_failed",
                            }
                        )
                        issues.append(
                            {
                                "code": "page_render_failed",
                                "page": page_number,
                                "reason": "page_load_failed",
                            }
                        )
                        continue
                try:
                    filename, payload, width, height = self._render_pymupdf_page(
                        module,
                        page,
                        page_number,
                        min(remaining_output, self.limits.max_rendered_page_bytes),
                    )
                    _private_write(output_dir / filename, payload)
                except _CommandFailure as exc:
                    pages.append(
                        {"page": page_number, "status": "failed", "issue": exc.code}
                    )
                    issues.append(
                        {
                            "code": "page_render_failed",
                            "page": page_number,
                            "reason": exc.code,
                        }
                    )
                    continue
                output_bytes += len(payload)
                pages.append(
                    {
                        "page": page_number,
                        "status": "rendered",
                        "descriptor": self._descriptor(
                            kind="local_image",
                            path=relative_output_dir / filename,
                            media_type="image/png",
                            size=len(payload),
                            sha256=hashlib.sha256(payload).hexdigest(),
                            attachment_id=attachment_id,
                            source_sha256=source_sha256,
                            page_number=page_number,
                            width=width,
                            height=height,
                        ),
                    }
                )

            complete = len(pages) == total_pages and not issues and all(
                page["status"] in {"text", "rendered"} for page in pages
            )
            return {
                "status": "complete" if complete else "partial",
                "processor": "pymupdf",
                "total_pages": total_pages,
                "processed_pages": len(pages),
                "pages": pages,
                "issues": issues,
                "output_bytes": output_bytes,
            }
        finally:
            close = getattr(document, "close", None)
            if callable(close):
                close()

    def _render_pymupdf_page(
        self,
        module: Any,
        page: Any,
        page_number: int,
        max_output: int,
    ) -> tuple[str, bytes, int, int]:
        if max_output < 1:
            raise _CommandFailure("output_limit")
        scale = self.limits.render_dpi / 72.0
        try:
            width = math.ceil(float(page.rect.width) * scale)
            height = math.ceil(float(page.rect.height) * scale)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise _CommandFailure("render_dimensions") from exc
        if width < 1 or height < 1 or width * height > self.limits.max_image_pixels:
            raise _CommandFailure("render_dimensions")
        try:
            matrix = module.Matrix(scale, scale)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            payload = pixmap.tobytes("png")
        except Exception as exc:
            raise _CommandFailure("pymupdf_error") from exc
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= max_output:
            raise _CommandFailure("output_limit")
        filename = f"page-{page_number:04d}.png"
        return filename, payload, int(pixmap.width), int(pixmap.height)

    def _inspect(self, pdf_path: Path, cwd: Path, deadline: float) -> dict[str, int]:
        assert self.toolchain.pdfinfo is not None
        result = self._command(
            (*self.toolchain.pdfinfo, "-isodates", str(pdf_path)),
            cwd=cwd,
            deadline=deadline,
            max_stdout=64 * 1024,
        )
        if result.returncode != 0:
            raise PDFRejected("malformed_pdf")
        try:
            output = result.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PDFRejected("invalid_pdf_metadata") from exc
        fields: dict[str, str] = {}
        for line in output.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key.strip().lower()] = value.strip()
        if fields.get("encrypted", "").lower().startswith("yes"):
            raise PDFRejected("encrypted_pdf")
        try:
            pages = int(fields["pages"])
        except (KeyError, ValueError) as exc:
            raise PDFRejected("invalid_pdf_page_count") from exc
        if pages < 1:
            raise PDFRejected("invalid_pdf_page_count")
        return {"pages": pages}

    def _extract_text(
        self,
        pdf_path: Path,
        cwd: Path,
        page_number: int,
        deadline: float,
        max_output: int,
    ) -> str:
        assert self.toolchain.pdftotext is not None
        if max_output < 1:
            raise _CommandFailure("output_limit")
        result = self._command(
            (
                *self.toolchain.pdftotext,
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-layout",
                "-enc",
                "UTF-8",
                str(pdf_path),
                "-",
            ),
            cwd=cwd,
            deadline=deadline,
            max_stdout=max_output,
        )
        if result.returncode != 0:
            raise _CommandFailure("nonzero_exit")
        return _normalize_text(result.stdout)

    def _render_page(
        self,
        pdf_path: Path,
        output_dir: Path,
        page_number: int,
        deadline: float,
        max_output: int,
    ) -> tuple[str, int, int, int]:
        assert self.toolchain.pdftoppm is not None
        if max_output < 1:
            raise _CommandFailure("output_limit")
        prefix = output_dir / f".render-{page_number:04d}"
        expected = prefix.with_suffix(".png")
        result = self._command(
            (
                *self.toolchain.pdftoppm,
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-singlefile",
                "-r",
                str(self.limits.render_dpi),
                "-png",
                str(pdf_path),
                str(prefix),
            ),
            cwd=output_dir,
            deadline=deadline,
            max_stdout=64 * 1024,
        )
        if result.returncode != 0 or not expected.exists():
            expected.unlink(missing_ok=True)
            raise _CommandFailure("nonzero_exit")
        try:
            size = expected.stat(follow_symlinks=False).st_size
            if size < 1 or size > max_output:
                raise _CommandFailure("output_limit")
            width, height = _validate_rendered_png(
                expected, self.limits.max_image_pixels
            )
            final_name = f"page-{page_number:04d}.png"
            final_path = output_dir / final_name
            os.chmod(expected, 0o600, follow_symlinks=False)
            os.replace(expected, final_path)
            return final_name, size, width, height
        except BaseException:
            expected.unlink(missing_ok=True)
            raise

    def _command(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        deadline: float,
        max_stdout: int,
    ) -> _CommandResult:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _CommandFailure("timeout")
        return _run_bounded(
            command,
            cwd=cwd,
            timeout=min(remaining, self.limits.command_timeout_seconds),
            max_stdout=max_stdout,
            max_stderr=self.limits.max_stderr_bytes,
        )

    @staticmethod
    def _descriptor(
        *,
        kind: str,
        path: PurePosixPath,
        media_type: str,
        size: int,
        sha256: str,
        attachment_id: str,
        source_sha256: str,
        page_number: int,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "kind": kind,
            "path": path.as_posix(),
            "media_type": media_type,
            "size": size,
            "sha256": sha256,
            "provenance": {
                "attachment_id": attachment_id,
                "source_sha256": source_sha256,
                "page": page_number,
            },
        }
        if width is not None and height is not None:
            value["width"] = width
            value["height"] = height
        return value
