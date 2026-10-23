from pathlib import Path

from deepresearch_flow.ocr.base import OcrPage
from deepresearch_flow.ocr.runner import run_ocr


class _Backend:
    def ocr(self, file_path: Path):
        from deepresearch_flow.ocr.base import OcrResult

        return OcrResult(pages=[OcrPage(page_index=0, markdown="text", images={})])


def test_rerun_reuses_directory_left_without_full_md(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    out = tmp_path / "out"
    (out / "doc" / "images").mkdir(parents=True)
    (out / "doc" / "images" / "partial.png").write_bytes(b"x")

    stats, _ = run_ocr(_Backend(), pdf, out)

    assert stats["processed"] == 1
    assert (out / "doc" / "full.md").is_file()
    assert not (out / "doc_1").exists()
