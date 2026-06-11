"""Tests for OCR runner orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.ocr.base import OcrBackend, OcrPage, OcrResult
from deepresearch_flow.ocr.runner import (
    _merge_pages,
    _ocr_with_retry,
    _resolve_output_dir,
    discover_files,
    run_ocr,
)


# --- Fake backend for testing -------------------------------------------------


class FakeBackend:
    """Returns canned OcrResult for any file."""

    def __init__(self, pages: list[OcrPage] | None = None) -> None:
        self._pages = pages or []

    def ocr(self, file_path: Path) -> OcrResult:
        return OcrResult(pages=self._pages)


class FailingBackend:
    """Raises an error for every OCR request."""

    def ocr(self, file_path: Path) -> OcrResult:
        raise RuntimeError(f"boom: {file_path.name}")


class FakeProgress:
    """Simple progress recorder for runner tests."""

    def __init__(self) -> None:
        self.updates: list[int] = []

    def update(self, amount: int) -> None:
        self.updates.append(amount)


# --- Tests --------------------------------------------------------------------


class TestDiscoverFiles:
    def test_single_pdf(self, tmp_path: Path) -> None:
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF")
        files = discover_files(pdf)
        assert files == [pdf]

    def test_directory(self, tmp_path: Path) -> None:
        (tmp_path / "a.pdf").write_bytes(b"%PDF")
        (tmp_path / "b.png").write_bytes(b"\x89PNG")
        (tmp_path / "c.txt").write_text("skip me")
        files = discover_files(tmp_path)
        stems = {f.name for f in files}
        assert stems == {"a.pdf", "b.png"}

    def test_nonexistent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            discover_files(tmp_path / "nope")

    def test_unsupported_single_file_raises(self, tmp_path: Path) -> None:
        txt = tmp_path / "doc.txt"
        txt.write_text("hello")
        with pytest.raises(ValueError, match="Unsupported"):
            discover_files(txt)


class TestMergePages:
    def test_single_page(self) -> None:
        pages = [OcrPage(page_index=0, markdown="# Hello", images={})]
        md, images, missing = _merge_pages(pages)
        assert md == "# Hello"
        assert images == {}
        assert missing == []

    def test_multiple_pages_separator(self) -> None:
        pages = [
            OcrPage(page_index=0, markdown="Page 0", images={}),
            OcrPage(page_index=1, markdown="Page 1", images={}),
        ]
        md, images, missing = _merge_pages(pages)
        assert md == "Page 0\n\n---\n\nPage 1"

    def test_images_merged(self) -> None:
        pages = [
            OcrPage(
                page_index=0,
                markdown="![](images/page_0000_00_md.png)",
                images={"images/page_0000_00_md.png": b"img0"},
            ),
            OcrPage(
                page_index=1,
                markdown="![](images/page_0001_00_md.png)",
                images={"images/page_0001_00_md.png": b"img1"},
            ),
        ]
        md, images, missing = _merge_pages(pages)
        assert len(images) == 2
        assert images["images/page_0000_00_md.png"] == b"img0"
        assert images["images/page_0001_00_md.png"] == b"img1"

    def test_missing_images_collected(self) -> None:
        pages = [
            OcrPage(
                page_index=0,
                markdown="text",
                images={},
                missing_images=("images/page_0000_00_md.png",),
            ),
        ]
        md, images, missing = _merge_pages(pages)
        assert missing == ["images/page_0000_00_md.png"]


class TestResolveOutputDir:
    def test_basic(self, tmp_path: Path) -> None:
        out = _resolve_output_dir(tmp_path, "paper")
        assert out == tmp_path / "paper"

    def test_collision_appends_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "paper").mkdir()
        out = _resolve_output_dir(tmp_path, "paper")
        assert out == tmp_path / "paper_1"

    def test_multiple_collisions(self, tmp_path: Path) -> None:
        (tmp_path / "paper").mkdir()
        (tmp_path / "paper_1").mkdir()
        out = _resolve_output_dir(tmp_path, "paper")
        assert out == tmp_path / "paper_2"


class TestRunOcr:
    def test_single_file_writes_output(self, tmp_path: Path) -> None:
        pdf = tmp_path / "input" / "doc.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF")
        output_dir = tmp_path / "output"

        pages = [
            OcrPage(
                page_index=0,
                markdown="# Title\n\n![fig](images/page_0000_00_md.png)",
                images={"images/page_0000_00_md.png": b"\x89PNG"},
            ),
        ]
        backend = FakeBackend(pages)

        stats, _ = run_ocr(backend, pdf, output_dir)

        assert stats["processed"] == 1
        assert stats["failed"] == 0

        doc_dir = output_dir / "doc"
        assert (doc_dir / "full.md").exists()
        assert (doc_dir / "images" / "page_0000_00_md.png").exists()

        md_content = (doc_dir / "full.md").read_text()
        assert "# Title" in md_content
        assert "images/page_0000_00_md.png" in md_content

    def test_directory_processes_all_files(self, tmp_path: Path) -> None:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.pdf").write_bytes(b"%PDF")
        (input_dir / "b.pdf").write_bytes(b"%PDF")
        output_dir = tmp_path / "output"

        backend = FakeBackend([OcrPage(page_index=0, markdown="text", images={})])
        stats, _ = run_ocr(backend, input_dir, output_dir)

        assert stats["processed"] == 2
        assert (output_dir / "a" / "full.md").exists()
        assert (output_dir / "b" / "full.md").exists()

    def test_progress_updates_once_per_file(self, tmp_path: Path) -> None:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.pdf").write_bytes(b"%PDF")
        (input_dir / "b.pdf").write_bytes(b"%PDF")
        progress = FakeProgress()

        backend = FakeBackend([OcrPage(page_index=0, markdown="text", images={})])
        run_ocr(backend, input_dir, tmp_path / "output", progress=progress)

        assert progress.updates == [1, 1]

    def test_empty_result_skipped(self, tmp_path: Path) -> None:
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"%PDF")
        output_dir = tmp_path / "output"

        backend = FakeBackend([])  # No pages.
        stats, _ = run_ocr(backend, pdf, output_dir)

        assert stats["processed"] == 0
        assert stats["skipped"] == 1

    def test_missing_images_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        output_dir = tmp_path / "output"

        pages = [
            OcrPage(
                page_index=0,
                markdown="![fig](images/page_0000_00_md.png)",
                images={},
                missing_images=("images/page_0000_00_md.png",),
            ),
        ]
        backend = FakeBackend(pages)

        with caplog.at_level("WARNING"):
            run_ocr(backend, pdf, output_dir)

        assert "missing" in caplog.text.lower()

    def test_skip_existing_by_default(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        output_dir = tmp_path / "output"

        # First run: creates output.
        backend = FakeBackend([OcrPage(page_index=0, markdown="v1", images={})])
        run_ocr(backend, pdf, output_dir)
        assert (output_dir / "doc" / "full.md").read_text() == "v1"

        # Second run: skipped because output exists.
        backend2 = FakeBackend([OcrPage(page_index=0, markdown="v2", images={})])
        stats, _ = run_ocr(backend2, pdf, output_dir)
        assert stats["skipped"] == 1
        assert stats["processed"] == 0
        # Content unchanged.
        assert (output_dir / "doc" / "full.md").read_text() == "v1"

    def test_overwrite_replaces_existing(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        output_dir = tmp_path / "output"

        # First run.
        backend = FakeBackend([OcrPage(page_index=0, markdown="v1", images={})])
        run_ocr(backend, pdf, output_dir)

        # Second run with overwrite=True.
        backend2 = FakeBackend([OcrPage(page_index=0, markdown="v2", images={})])
        stats, _ = run_ocr(backend2, pdf, output_dir, overwrite=True)
        assert stats["processed"] == 1
        assert stats["skipped"] == 0
        assert (output_dir / "doc" / "full.md").read_text() == "v2"

    def test_progress_updates_for_failed_files(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        progress = FakeProgress()

        stats, _ = run_ocr(
            FailingBackend(),
            pdf,
            tmp_path / "output",
            max_retries=1,
            progress=progress,
        )

        assert stats["failed"] == 1
        assert progress.updates == [1]

    def test_retry_rejects_non_positive_attempt_limit(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        with pytest.raises(ValueError, match="max_retries"):
            _ocr_with_retry(FakeBackend(), pdf, max_retries=0)


class TestMigrateToHashNames:
    def test_renames_images_and_updates_md(self, tmp_path: Path) -> None:
        import hashlib

        from deepresearch_flow.ocr.runner import migrate_to_hash_names

        doc_dir = tmp_path / "paper"
        doc_dir.mkdir()
        img_dir = doc_dir / "images"
        img_dir.mkdir()

        # Create old-style image.
        img_data = b"\x89PNG fake image content"
        (img_dir / "page_0000_00_md.png").write_bytes(img_data)
        expected_hash = hashlib.sha256(img_data).hexdigest()[:12]

        # Create full.md referencing old name.
        (doc_dir / "full.md").write_text("# Title\n\n![fig](images/page_0000_00_md.png)\n")

        stats, _ = migrate_to_hash_names(tmp_path)

        assert stats["migrated"] == 1
        # Old file gone, new hash file exists.
        assert not (img_dir / "page_0000_00_md.png").exists()
        assert (img_dir / f"{expected_hash}.png").exists()
        # Markdown updated.
        md = (doc_dir / "full.md").read_text()
        assert f"images/{expected_hash}.png" in md
        assert "page_0000_00_md" not in md

    def test_skips_already_migrated(self, tmp_path: Path) -> None:
        import hashlib

        from deepresearch_flow.ocr.runner import migrate_to_hash_names

        doc_dir = tmp_path / "paper"
        doc_dir.mkdir()
        img_dir = doc_dir / "images"
        img_dir.mkdir()

        img_data = b"already hashed"
        digest = hashlib.sha256(img_data).hexdigest()[:12]
        (img_dir / f"{digest}.png").write_bytes(img_data)
        (doc_dir / "full.md").write_text(f"![](images/{digest}.png)\n")

        stats, _ = migrate_to_hash_names(tmp_path)
        assert stats["skipped"] == 1
        assert stats["migrated"] == 0

    def test_dry_run_no_changes(self, tmp_path: Path) -> None:
        from deepresearch_flow.ocr.runner import migrate_to_hash_names

        doc_dir = tmp_path / "paper"
        doc_dir.mkdir()
        img_dir = doc_dir / "images"
        img_dir.mkdir()

        (img_dir / "page_0000_00_md.png").write_bytes(b"data")
        (doc_dir / "full.md").write_text("![](images/page_0000_00_md.png)\n")

        stats, _ = migrate_to_hash_names(tmp_path, dry_run=True)
        assert stats["migrated"] == 1
        # But files are unchanged.
        assert (img_dir / "page_0000_00_md.png").exists()
        assert "page_0000_00_md" in (doc_dir / "full.md").read_text()
