from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
import sys
import time
import uuid
from pathlib import Path, PurePosixPath

import pytest

import ai_harness.attachments.pdf as pdf_module
import ai_harness.attachments.store as attachment_store_module
from ai_harness.attachments import (
    ABSOLUTE_MAX_FILE_BYTES,
    MAX_ATTACHMENTS,
    MAX_RUNTIME_IMAGE_REFERENCES,
    AttachmentLimitError,
    AttachmentLimits,
    AttachmentQuotaError,
    AttachmentRejected,
    AttachmentStorageError,
    AttachmentStore,
    IncomingAttachment,
    PDFLimits,
    PDFProcessor,
    PDFRejected,
    PDFToolchain,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
MINIMAL_PDF = b"%PDF-1.7\n% attachment test\n%%EOF\n"


class _FakeRect:
    width = 72
    height = 72


class _FakePixmap:
    width = 1
    height = 1

    def tobytes(self, output: str) -> bytes:
        assert output == "png"
        return PNG_1X1


class _FakePage:
    rect = _FakeRect()

    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, mode: str, *, sort: bool) -> str:
        assert mode == "text"
        assert sort is True
        return self.text

    def get_pixmap(self, *, matrix: object, alpha: bool) -> _FakePixmap:
        assert matrix is not None
        assert alpha is False
        return _FakePixmap()


class _FakeDocument:
    needs_pass = False

    def __init__(self, texts: list[str], *, encrypted: bool = False) -> None:
        self.pages = [_FakePage(text) for text in texts]
        self.page_count = len(self.pages)
        self.is_encrypted = encrypted
        self.closed = False

    def __len__(self) -> int:
        return len(self.pages)

    def load_page(self, index: int) -> _FakePage:
        return self.pages[index]

    def close(self) -> None:
        self.closed = True


class _FakePyMuPDF:
    def __init__(
        self,
        texts: list[str],
        *,
        encrypted: bool = False,
        fail_open: bool = False,
    ) -> None:
        self.texts = texts
        self.encrypted = encrypted
        self.fail_open = fail_open
        self.document: _FakeDocument | None = None

    def open(self, path: str) -> _FakeDocument:
        assert path.endswith(".pdf")
        if self.fail_open:
            raise RuntimeError("broken PDF")
        self.document = _FakeDocument(self.texts, encrypted=self.encrypted)
        return self.document

    @staticmethod
    def Matrix(x_scale: float, y_scale: float) -> tuple[float, float]:
        return x_scale, y_scale


def _incoming(name: str, payload: bytes, mime: str | None = None) -> IncomingAttachment:
    return IncomingAttachment(name, io.BytesIO(payload), mime)


def _pdf_processor(
    texts: list[str],
    *,
    limits: PDFLimits | None = None,
    encrypted: bool = False,
    fail_open: bool = False,
) -> tuple[PDFProcessor, _FakePyMuPDF]:
    module = _FakePyMuPDF(texts, encrypted=encrypted, fail_open=fail_open)
    return (
        PDFProcessor(
            backend="pymupdf",
            pymupdf_module=module,
            limits=limits,
        ),
        module,
    )


def test_attachment_limits_have_hard_caps() -> None:
    defaults = AttachmentLimits()

    assert defaults.max_files == MAX_ATTACHMENTS == 5
    assert defaults.max_file_bytes == 100 * 1024 * 1024
    assert defaults.max_task_bytes == 500 * 1024 * 1024
    assert defaults.max_runtime_image_bytes == 10 * 1024 * 1024
    AttachmentLimits(max_file_bytes=ABSOLUTE_MAX_FILE_BYTES)

    with pytest.raises(ValueError):
        AttachmentLimits(max_files=6)
    with pytest.raises(ValueError):
        AttachmentLimits(max_file_bytes=ABSOLUTE_MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError):
        AttachmentLimits(max_runtime_image_bytes=10 * 1024 * 1024 + 1)


def test_text_staging_is_private_streamed_and_contains_no_raw_metadata(
    tmp_path: Path,
) -> None:
    store = AttachmentStore(tmp_path / "staging")
    payload = b"confidential value belongs only in the file\n"

    staged = store.stage([_incoming("my resume.md", payload, "text/markdown")])

    attachment = staged.manifest["attachments"][0]
    assert attachment["safe_name"] == "my_resume.md"
    assert attachment["media_type"] == "text/markdown"
    assert attachment["size"] == len(payload)
    assert attachment["sha256"] == hashlib.sha256(payload).hexdigest()
    assert attachment["content"][0]["kind"] == "text"
    assert not PurePosixPath(attachment["path"]).is_absolute()
    manifest_text = staged.manifest_path.read_text(encoding="utf-8")
    assert "confidential value" not in manifest_text
    assert stat.S_IMODE(store.staging_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(staged.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((staged.root / attachment["path"]).stat().st_mode) == 0o600
    assert store.load(staged.set_id).manifest == staged.manifest


def test_non_ascii_filename_uses_neutral_private_storage_name(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "staging")

    staged = store.stage([_incoming("техническое задание.txt", b"context\n")])

    attachment = staged.manifest["attachments"][0]
    assert attachment["safe_name"] == "attachment.txt"
    assert attachment["kind"] == "text"


def test_attachment_count_file_and_task_limits_cleanup_partial_staging(
    tmp_path: Path,
) -> None:
    limits = AttachmentLimits(
        max_files=2,
        max_file_bytes=10,
        max_task_bytes=15,
        chunk_bytes=4096,
        max_runtime_image_bytes=10,
    )
    store = AttachmentStore(tmp_path / "staging", limits=limits)

    with pytest.raises(AttachmentLimitError, match="count"):
        store.stage([_incoming(f"{index}.txt", b"x") for index in range(3)])
    with pytest.raises(AttachmentLimitError, match="file_limit"):
        store.stage([_incoming("large.txt", b"x" * 11)])
    with pytest.raises(AttachmentLimitError, match="task_limit"):
        store.stage([_incoming("one.txt", b"x" * 8), _incoming("two.txt", b"y" * 8)])
    assert [path for path in store.staging_root.iterdir() if path.name != ".quota.lock"] == []


def test_pending_upload_pool_has_set_and_byte_quotas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AttachmentStore(tmp_path / "staging")
    first = store.stage([_incoming("first.txt", b"first")])

    monkeypatch.setattr(attachment_store_module, "MAX_PENDING_ATTACHMENT_SETS", 1)
    with pytest.raises(AttachmentQuotaError, match="set_quota"):
        store.stage([_incoming("second.txt", b"second")])

    monkeypatch.setattr(attachment_store_module, "MAX_PENDING_ATTACHMENT_SETS", 32)
    _sets, used = store._pending_usage()
    monkeypatch.setattr(
        attachment_store_module, "MAX_PENDING_ATTACHMENT_BYTES", used + 4
    )
    with pytest.raises(AttachmentQuotaError, match="byte_quota"):
        store.stage([_incoming("large.txt", b"123456789")])

    assert store.load(first.set_id).manifest == first.manifest
    assert [
        path.name
        for path in store.staging_root.iterdir()
        if path.name not in {".quota.lock", first.set_id}
    ] == []


def test_verified_staged_set_is_rechecked_against_tightened_consumer_limits(
    tmp_path: Path,
) -> None:
    staging_root = tmp_path / "staging"
    staged = AttachmentStore(
        staging_root,
        limits=AttachmentLimits(max_file_bytes=10, max_task_bytes=20),
    ).stage([_incoming("context.txt", b"123456789")])
    consumer = AttachmentStore(
        staging_root,
        limits=AttachmentLimits(max_file_bytes=5, max_task_bytes=10),
    )

    loaded = consumer.load(staged.set_id)
    with pytest.raises(AttachmentLimitError, match="file_limit"):
        consumer.validate_set_limits(loaded)


@pytest.mark.parametrize(
    "filename",
    [
        "../secret.txt",
        "folder/secret.txt",
        "folder\\secret.txt",
        "/absolute.txt",
        "C:\\absolute.txt",
        "evil.zip",
        "program.exe",
        "active.svg",
        "unknown.dat",
    ],
)
def test_unsafe_names_and_types_are_rejected(tmp_path: Path, filename: str) -> None:
    store = AttachmentStore(tmp_path / "staging")

    with pytest.raises(AttachmentRejected):
        store.stage([_incoming(filename, b"plain text")])


@pytest.mark.parametrize(
    ("filename", "payload", "declared_mime"),
    [
        ("archive.txt", b"PK\x03\x04payload", None),
        ("binary.txt", b"\x7fELFpayload", None),
        ("script.py", b"#!/usr/bin/python3\nprint('x')\n", None),
        ("fake.png", b"plain text", "image/png"),
        ("notes.txt", PNG_1X1, "text/plain"),
        ("notes.txt", b"plain text", "image/png"),
    ],
)
def test_archive_executable_and_mime_spoof_content_is_rejected(
    tmp_path: Path,
    filename: str,
    payload: bytes,
    declared_mime: str | None,
) -> None:
    store = AttachmentStore(tmp_path / "staging")

    with pytest.raises(AttachmentRejected):
        store.stage([_incoming(filename, payload, declared_mime)])


def test_path_sources_reject_symlinks_and_executable_modes(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "staging")
    source = tmp_path / "source.txt"
    source.write_text("safe text", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(source)

    with pytest.raises(AttachmentRejected, match="symlink"):
        store.stage_paths([link])

    source.chmod(0o700)
    with pytest.raises(AttachmentRejected, match="executable"):
        store.stage_paths([source])


def test_image_descriptor_is_neutral_validated_and_runtime_bounded(
    tmp_path: Path,
) -> None:
    limits = AttachmentLimits(
        max_file_bytes=1024,
        max_task_bytes=2048,
        chunk_bytes=4096,
        max_runtime_image_bytes=1024,
    )
    store = AttachmentStore(tmp_path / "staging", limits=limits)

    staged = store.stage([_incoming("context.png", PNG_1X1, "image/png")])

    attachment = staged.manifest["attachments"][0]
    descriptor = attachment["content"][0]
    assert descriptor["kind"] == "local_image"
    assert descriptor["media_type"] == "image/png"
    assert descriptor["size"] == len(PNG_1X1)
    assert descriptor["sha256"] == hashlib.sha256(PNG_1X1).hexdigest()
    assert descriptor["width"] == descriptor["height"] == 1
    assert store.resolve_descriptor_path(staged, descriptor).is_file()

    strict = AttachmentStore(
        tmp_path / "strict",
        limits=AttachmentLimits(
            max_file_bytes=1024,
            max_task_bytes=2048,
            chunk_bytes=4096,
            max_runtime_image_bytes=len(PNG_1X1) - 1,
        ),
    )
    with pytest.raises(AttachmentLimitError, match="runtime_payload"):
        strict.stage([_incoming("context.png", PNG_1X1)])


def test_bind_to_run_is_atomic_one_time_and_revalidates_files(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "staging")
    staged = store.stage([_incoming("notes.txt", b"bind me")])
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)

    bound = store.bind_to_run(staged.set_id, run_dir / "inputs")

    assert not staged.root.exists()
    assert bound.root == run_dir / "inputs"
    descriptor = bound.manifest["attachments"][0]["content"][0]
    assert store.resolve_descriptor_path(bound, descriptor).read_bytes() == b"bind me"
    with pytest.raises(AttachmentStorageError, match="unavailable"):
        store.bind_to_run(staged.set_id, run_dir / "inputs-2")

    second = store.stage([_incoming("notes.txt", b"original")])
    raw = second.root / second.manifest["attachments"][0]["path"]
    raw.write_bytes(b"tampered")
    (tmp_path / "runs" / "run-2").mkdir()
    with pytest.raises(AttachmentStorageError, match="mismatch"):
        store.bind_to_run(second.set_id, tmp_path / "runs" / "run-2" / "inputs")


def test_restore_from_run_rolls_back_a_just_bound_set_atomically(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "staging")
    staged = store.stage([_incoming("notes.txt", b"rollback me")])
    run_dir = tmp_path / "runs" / "run-rollback"
    run_dir.mkdir(parents=True)
    bound = store.bind_to_run(staged.set_id, run_dir / "inputs")

    restored = store.restore_from_run(staged.set_id, bound.root)

    assert restored.root == staged.root
    assert restored.root.is_dir()
    assert not bound.root.exists()
    descriptor = restored.manifest["attachments"][0]["content"][0]
    assert store.resolve_descriptor_path(restored, descriptor).read_bytes() == b"rollback me"


def test_discard_staged_and_bound_run_remove_only_revalidated_sets(
    tmp_path: Path,
) -> None:
    store = AttachmentStore(tmp_path / "staging")
    first = store.stage([_incoming("first.txt", b"first")])
    second = store.stage([_incoming("second.txt", b"second")])
    combined = store.combine([first.set_id, second.set_id])
    run_dir = tmp_path / "runs" / "run-discard"
    run_dir.mkdir(parents=True)
    bound = store.bind_to_run(combined.set_id, run_dir / "inputs")

    store.discard_bound_run(combined.set_id, bound.root)
    removed = store.discard_staged([first.set_id, second.set_id])

    assert removed == (first.set_id, second.set_id)
    assert not bound.root.exists()
    assert not first.root.exists()
    assert not second.root.exists()


def test_combine_supports_sequential_uploads_and_optional_source_discard(
    tmp_path: Path,
) -> None:
    store = AttachmentStore(tmp_path / "staging")
    first = store.stage([_incoming("first.txt", b"first")])
    second = store.stage([_incoming("second.md", b"second")])

    combined = store.combine([first.set_id, second.set_id])

    assert combined.manifest["attachment_count"] == 2
    assert [
        attachment["safe_name"] for attachment in combined.manifest["attachments"]
    ] == ["first.txt", "second.md"]
    assert first.root.exists() and second.root.exists()

    consumed = store.combine([first.set_id, second.set_id], discard_sources=True)

    assert consumed.manifest["attachment_count"] == 2
    assert not first.root.exists() and not second.root.exists()


def test_combine_rejects_duplicate_sets_and_more_than_five_files(
    tmp_path: Path,
) -> None:
    store = AttachmentStore(tmp_path / "staging")
    single = store.stage([_incoming("single.txt", b"single")])
    with pytest.raises(AttachmentRejected, match="duplicate"):
        store.combine([single.set_id, single.set_id])

    first = store.stage([_incoming(f"a-{index}.txt", b"a") for index in range(3)])
    second = store.stage([_incoming(f"b-{index}.txt", b"b") for index in range(3)])
    with pytest.raises(AttachmentLimitError, match="count"):
        store.combine([first.set_id, second.set_id])


def test_ttl_cleanup_removes_staging_without_following_symlinks(tmp_path: Path) -> None:
    limits = AttachmentLimits(ttl_seconds=10)
    store = AttachmentStore(tmp_path / "staging", limits=limits)
    staged = store.stage([_incoming("notes.txt", b"expire me")])
    created = float(staged.manifest["created_at_epoch"])

    assert store.cleanup_expired(now=created + 11) == (staged.set_id,)
    assert not staged.root.exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    malicious_id = uuid.uuid4().hex
    link = store.staging_root / malicious_id
    link.symlink_to(outside, target_is_directory=True)

    removed = store.cleanup_expired(now=time.time() + limits.ttl_seconds + 1)

    assert malicious_id in removed
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not os.path.lexists(link)


def test_pymupdf_extracts_text_and_renders_only_scanned_pages(tmp_path: Path) -> None:
    processor, module = _pdf_processor(
        ["This page has a useful searchable text layer.", "   \n\t"]
    )
    store = AttachmentStore(tmp_path / "staging", pdf_processor=processor)

    staged = store.stage([_incoming("document.pdf", MINIMAL_PDF, "application/pdf")])

    attachment = staged.manifest["attachments"][0]
    pdf = attachment["pdf"]
    assert pdf["status"] == staged.status == "complete"
    assert pdf["processor"] == "pymupdf"
    assert [page["status"] for page in pdf["pages"]] == ["text", "rendered"]
    assert [item["kind"] for item in attachment["content"]] == [
        "text",
        "local_image",
    ]
    for page_number, descriptor in enumerate(attachment["content"], start=1):
        assert descriptor["provenance"] == {
            "attachment_id": attachment["id"],
            "source_sha256": attachment["sha256"],
            "page": page_number,
        }
        assert store.resolve_descriptor_path(staged, descriptor).is_file()
    assert module.document is not None and module.document.closed is True


def test_real_pymupdf_text_and_scanned_page_smoke(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    source = tmp_path / "real.pdf"
    document = pymupdf.open()
    text_page = document.new_page(width=300, height=300)
    text_page.insert_text(
        (40, 80), "This page has a real searchable text layer for context."
    )
    document.new_page(width=300, height=300)
    document.save(source)
    document.close()
    store = AttachmentStore(tmp_path / "staging")

    staged = store.stage_paths([source])

    pdf = staged.manifest["attachments"][0]["pdf"]
    assert pdf["processor"] == "pymupdf"
    assert pdf["status"] == "complete"
    assert [page["status"] for page in pdf["pages"]] == ["text", "rendered"]


@pytest.mark.parametrize(
    ("program", "expected_error"),
    [
        ("import time\ntime.sleep(30)\n", "pymupdf_worker_timeout"),
        (
            "import os\nos.write(1, b'x' * 4096)\n",
            "pymupdf_worker_output_limit",
        ),
        (
            "import sys\nsys.stderr.write('worker failed')\nraise SystemExit(7)\n",
            "pymupdf_worker_failed",
        ),
    ],
)
def test_isolated_pymupdf_bounds_hangs_output_and_worker_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    program: str,
    expected_error: str,
) -> None:
    helper = tmp_path / "fake-pymupdf-worker.py"
    helper.write_text(program, encoding="utf-8")
    source = tmp_path / "source.pdf"
    source.write_bytes(MINIMAL_PDF)
    output = tmp_path / "derived"
    processor = PDFProcessor(
        backend="pymupdf",
        limits=PDFLimits(
            max_pages=1,
            max_total_pages=1,
            timeout_seconds=0.25,
            command_timeout_seconds=0.25,
        ),
    )
    monkeypatch.setattr(processor, "_pymupdf_available", lambda: True)
    monkeypatch.setattr(
        processor,
        "_pymupdf_helper_command",
        lambda *args, **kwargs: (sys.executable, str(helper)),
    )
    monkeypatch.setattr(pdf_module, "_PYMUPDF_HELPER_MAX_STDOUT", 1024)

    started = time.monotonic()
    with pytest.raises(PDFRejected, match=expected_error):
        processor.process(
            source,
            output,
            relative_output_dir=PurePosixPath("derived") / ("a" * 32),
            attachment_id="a" * 32,
            source_sha256=hashlib.sha256(MINIMAL_PDF).hexdigest(),
        )

    assert time.monotonic() - started < 3
    assert not os.path.lexists(output)


def test_pdf_page_limit_has_explicit_partial_status_and_provenance(
    tmp_path: Path,
) -> None:
    limits = PDFLimits(max_pages=2, max_total_pages=10)
    processor, _ = _pdf_processor(
        ["searchable page one text", "searchable page two text", "page three text"],
        limits=limits,
    )
    store = AttachmentStore(tmp_path / "staging", pdf_processor=processor)

    staged = store.stage([_incoming("document.pdf", MINIMAL_PDF)])

    pdf = staged.manifest["attachments"][0]["pdf"]
    assert staged.status == pdf["status"] == "partial"
    assert pdf["total_pages"] == 3
    assert pdf["processed_pages"] == 2
    assert pdf["issues"] == [{"code": "page_limit_exceeded", "unprocessed_pages": 1}]
    with pytest.raises(AttachmentRejected, match="processing_incomplete"):
        store.validate_runtime_ready(staged)


def test_complete_scanned_pdf_reports_runtime_image_reference_count(
    tmp_path: Path,
) -> None:
    processor, _module = _pdf_processor(
        [""] * (MAX_RUNTIME_IMAGE_REFERENCES + 1)
    )
    store = AttachmentStore(tmp_path / "staging", pdf_processor=processor)

    staged = store.stage([_incoming("scanned.pdf", MINIMAL_PDF)])

    assert staged.status == "complete"
    assert store.validate_runtime_ready(staged) == MAX_RUNTIME_IMAGE_REFERENCES + 1


def test_encrypted_and_malformed_pdfs_are_rejected_and_cleaned(
    tmp_path: Path,
) -> None:
    encrypted, _ = _pdf_processor(["secret page"], encrypted=True)
    store = AttachmentStore(tmp_path / "encrypted", pdf_processor=encrypted)
    with pytest.raises(PDFRejected, match="encrypted"):
        store.stage([_incoming("document.pdf", MINIMAL_PDF)])
    assert [path for path in store.staging_root.iterdir() if path.name != ".quota.lock"] == []

    malformed, _ = _pdf_processor([], fail_open=True)
    broken_store = AttachmentStore(tmp_path / "malformed", pdf_processor=malformed)
    with pytest.raises(PDFRejected, match="malformed"):
        broken_store.stage([_incoming("document.pdf", MINIMAL_PDF)])
    with pytest.raises(AttachmentRejected, match="malformed"):
        broken_store.stage([_incoming("document.pdf", b"%PDF-1.7\nno eof")])
    assert [
        path for path in broken_store.staging_root.iterdir() if path.name != ".quota.lock"
    ] == []


def test_poppler_fallback_bounds_subprocess_output_and_reports_partial(
    tmp_path: Path,
) -> None:
    tool = tmp_path / "fake_pdf_tool.py"
    tool.write_text(
        "\n".join(
            [
                "import base64",
                "import pathlib",
                "import sys",
                "mode = sys.argv[1]",
                "if mode == 'info':",
                "    print('Pages: 1')",
                "    print('Encrypted: no')",
                "elif mode == 'text':",
                "    sys.stdout.write('x' * 10000)",
                "elif mode == 'render':",
                f"    payload = base64.b64decode({base64.b64encode(PNG_1X1).decode('ascii')!r})",
                "    pathlib.Path(sys.argv[-1] + '.png').write_bytes(payload)",
            ]
        ),
        encoding="utf-8",
    )
    command = (sys.executable, str(tool))
    processor = PDFProcessor(
        backend="poppler",
        toolchain=PDFToolchain(
            pdfinfo=(*command, "info"),
            pdftotext=(*command, "text"),
            pdftoppm=(*command, "render"),
        ),
        limits=PDFLimits(
            max_pages=1,
            max_total_pages=10,
            timeout_seconds=5,
            command_timeout_seconds=2,
            max_text_page_bytes=32,
            min_text_chars=5,
        ),
    )
    store = AttachmentStore(tmp_path / "staging", pdf_processor=processor)

    staged = store.stage([_incoming("document.pdf", MINIMAL_PDF)])

    pdf = staged.manifest["attachments"][0]["pdf"]
    assert pdf["processor"] == "poppler"
    assert pdf["status"] == staged.status == "partial"
    assert pdf["pages"][0]["status"] == "rendered"
    assert pdf["issues"] == [
        {"code": "text_extraction_failed", "page": 1, "reason": "output_limit"}
    ]
