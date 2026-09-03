from __future__ import annotations

import argparse
import json
import math
import resource
import sys
from pathlib import Path, PurePosixPath
from typing import Any


_MIB = 1024 * 1024
_MAX_LIMITS_JSON_BYTES = 16 * 1024
_SAFE_PDF_ERRORS = frozenset(
    {
        "encrypted_pdf",
        "invalid_pdf_page_count",
        "malformed_pdf",
        "pdf_page_count_exceeds_hard_limit",
        "pymupdf_unavailable",
    }
)


def _emit(value: dict[str, Any]) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    sys.stdout.write(payload)
    sys.stdout.flush()


def _set_hard_limit(kind: int, requested: int) -> None:
    if requested < 1:
        raise ValueError("invalid resource limit")
    _soft, current_hard = resource.getrlimit(kind)
    target = requested
    if current_hard != resource.RLIM_INFINITY:
        target = min(target, int(current_hard))
    if target < 1:
        raise ValueError("resource limit unavailable")
    resource.setrlimit(kind, (target, target))


def _apply_resource_limits(
    *,
    timeout_seconds: float,
    address_space_bytes: int,
    file_size_bytes: int,
) -> None:
    # Apply limits before importing the native parser. The wall-clock watchdog
    # remains in the parent and kills the entire worker process group.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    _set_hard_limit(resource.RLIMIT_CPU, max(1, math.ceil(timeout_seconds) + 1))
    # RLIMIT_AS is not a safe process-memory ceiling on macOS: dyld reserves a
    # very large virtual address range before this worker starts, so a finite
    # limit can kill an otherwise tiny import. Linux accounts it predictably.
    if sys.platform.startswith("linux") and hasattr(resource, "RLIMIT_AS"):
        _set_hard_limit(resource.RLIMIT_AS, address_space_bytes)
    _set_hard_limit(resource.RLIMIT_FSIZE, file_size_bytes)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--relative-output-dir", required=True)
    parser.add_argument("--attachment-id", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--limits-json", required=True)
    parser.add_argument("--address-space-bytes", required=True, type=int)
    return parser.parse_args()


def main() -> int:
    try:
        arguments = _arguments()
        encoded_limits = arguments.limits_json.encode("utf-8", errors="strict")
        if len(encoded_limits) > _MAX_LIMITS_JSON_BYTES:
            raise ValueError("limits payload too large")
        limits_value = json.loads(encoded_limits)
        if not isinstance(limits_value, dict):
            raise ValueError("invalid limits payload")
        timeout_seconds = float(limits_value["timeout_seconds"])
        file_size_bytes = int(limits_value["max_output_bytes"])
        address_space_bytes = int(arguments.address_space_bytes)
        # Leave enough memory for a maximum-size decoded raster plus parser
        # bookkeeping, but never accept an effectively unbounded parent value.
        if not 256 * _MIB <= address_space_bytes <= 4 * 1024 * _MIB:
            raise ValueError("invalid address-space limit")
        _apply_resource_limits(
            timeout_seconds=timeout_seconds,
            address_space_bytes=address_space_bytes,
            file_size_bytes=file_size_bytes,
        )
    except Exception:
        _emit({"ok": False, "error": "pymupdf_worker_resource_limit"})
        return 2

    # Executing this file by absolute path does not put the package root on
    # sys.path. Add only its fixed source root after resource limits are active.
    source_root = str(Path(__file__).resolve().parents[2])
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

    try:
        from ai_harness.attachments.models import PDFRejected
        from ai_harness.attachments.pdf import PDFLimits, PDFProcessor
    except Exception:
        _emit({"ok": False, "error": "pymupdf_worker_failed"})
        return 4

    try:
        limits = PDFLimits(**limits_value)
        module = PDFProcessor._load_pymupdf()
        if module is None:
            raise PDFRejected("pymupdf_unavailable")
        processor = PDFProcessor(
            backend="pymupdf",
            pymupdf_module=module,
            limits=limits,
        )
        result = processor.process(
            Path(arguments.pdf),
            Path(arguments.output_dir),
            relative_output_dir=PurePosixPath(arguments.relative_output_dir),
            attachment_id=arguments.attachment_id,
            source_sha256=arguments.source_sha256,
        )
    except PDFRejected as exc:
        error = str(exc)
        _emit(
            {
                "ok": False,
                "error": (
                    error if error in _SAFE_PDF_ERRORS else "pymupdf_worker_failed"
                ),
            }
        )
        return 3
    except Exception:
        _emit({"ok": False, "error": "pymupdf_worker_failed"})
        return 4

    _emit({"ok": True, "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
