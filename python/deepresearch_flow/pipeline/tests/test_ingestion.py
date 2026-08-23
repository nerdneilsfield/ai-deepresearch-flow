from io import BytesIO
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from deepresearch_flow.pipeline.artifacts import ArtifactStore
from deepresearch_flow.pipeline.config import ModelAllowlist, PipelineConfig
from deepresearch_flow.pipeline.ingestion import BatchIngestor, UploadPart
from deepresearch_flow.pipeline.state import PipelineState


class TinyChunks:
    def __init__(self, value: bytes):
        self.value = value

    def read(self, size: int = -1, /) -> bytes:
        del size
        if not self.value:
            return b""
        chunk, self.value = self.value[:2], self.value[2:]
        return chunk


class FailingStream:
    def __init__(self) -> None:
        self.calls = 0

    def read(self, size: int = -1, /) -> bytes:
        del size
        self.calls += 1
        if self.calls == 1:
            return b"%PDF-partial"
        raise OSError("stream failed")


def build_ingestor(
    tmp_path: Path,
    *,
    pdfs_per_batch: int = 3,
    max_pdf_bytes: int = 32,
    max_batch_bytes: int = 64,
    bibtex_max_bytes: int = 512,
) -> tuple[BatchIngestor, PipelineState, ArtifactStore]:
    config = PipelineConfig(
        enabled=True,
        pdfs_per_batch=pdfs_per_batch,
        max_pdf_bytes=max_pdf_bytes,
        max_batch_bytes=max_batch_bytes,
        bibtex_max_bytes=bibtex_max_bytes,
        ocr=ModelAllowlist(("ocr-a",), "ocr-a"),
        extract=ModelAllowlist(("extract-a",), "extract-a"),
        translate=ModelAllowlist(("translate-a",), "translate-a"),
    )
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    return BatchIngestor(config, state, artifacts), state, artifacts


def test_ingests_multiple_pdfs_in_streams_and_keeps_display_names(tmp_path: Path) -> None:
    service, state, artifacts = build_ingestor(tmp_path)

    result = service.ingest(
        [
            UploadPart("first paper.pdf", BytesIO(b"%PDF-1.7 first")),
            UploadPart("second paper.pdf", BytesIO(b"%PDF-1.7 second")),
        ],
        selected_models={"ocr": "ocr-a", "extract": "extract-a", "translate": "translate-a"},
    )

    assert len(result.jobs) == 2
    assert all(state.get_job(job_id)["status"] == "queued" for job_id in result.jobs)
    assert [state.get_job_input(job_id)["filename"] for job_id in result.jobs] == [
        "first paper.pdf",
        "second paper.pdf",
    ]
    for job_id in result.jobs:
        artifact = artifacts.resolve(job_id, "pdf")
        assert artifact is not None and artifact.path.parent.name == job_id
        assert artifact.path.name.startswith("pdf-")


def test_accepts_file_like_streams_that_return_small_chunks(tmp_path: Path) -> None:
    service, _, _ = build_ingestor(tmp_path)
    result = service.ingest([UploadPart("paper.pdf", cast(BinaryIO, TinyChunks(b"%PDF-1.7 paper")))])
    assert len(result.jobs) == 1


def test_absent_bibtex_is_explicitly_not_provided(tmp_path: Path) -> None:
    service, state, _ = build_ingestor(tmp_path)

    result = service.ingest([UploadPart("paper.pdf", BytesIO(b"%PDF-1.7 paper"))])

    assert result.bibtex_status == "not_provided"
    assert state.list_bibtex_entries(result.batch_id) == []


def test_rejects_empty_batch(tmp_path: Path) -> None:
    service, state, _ = build_ingestor(tmp_path)
    with pytest.raises(ValueError, match="count"):
        service.ingest([])
    assert state.list_batches() == []


@pytest.mark.parametrize(
    "filename,content,reason",
    [
        ("paper.txt", b"%PDF-1.7", "extension"),
        ("paper.pdf", b"", "empty"),
        ("paper.pdf", b"not pdf", "header"),
        ("paper.pdf", b"%PDF-1.7" + b"x" * 40, "size"),
    ],
)
def test_rejects_invalid_pdf_and_removes_only_incomplete_batch(
    tmp_path: Path, filename: str, content: bytes, reason: str
) -> None:
    service, state, artifacts = build_ingestor(tmp_path, max_pdf_bytes=32)

    with pytest.raises(ValueError, match=reason):
        service.ingest([UploadPart(filename, BytesIO(content))])

    assert list((tmp_path / "work").iterdir()) == []
    assert state.list_batches() == []
    assert not list(artifacts.formal_root.iterdir())


def test_rejects_aggregate_overflow_and_duplicate_bytes(tmp_path: Path) -> None:
    service, state, _ = build_ingestor(tmp_path, max_batch_bytes=20)
    with pytest.raises(ValueError, match="batch"):
        service.ingest(
            [
                UploadPart("one.pdf", BytesIO(b"%PDF-1.7 one")),
                UploadPart("two.pdf", BytesIO(b"%PDF-1.7 two")),
            ]
        )
    assert state.list_batches() == []

    service, state, _ = build_ingestor(tmp_path / "duplicate")
    with pytest.raises(ValueError, match="duplicate"):
        service.ingest(
            [
                UploadPart("one.pdf", BytesIO(b"%PDF-1.7 same")),
                UploadPart("renamed.pdf", BytesIO(b"%PDF-1.7 same")),
            ]
        )
    assert state.list_batches() == []


def test_rejects_invalid_or_oversized_bibtex(tmp_path: Path) -> None:
    service, state, _ = build_ingestor(tmp_path, bibtex_max_bytes=40)
    with pytest.raises(ValueError, match="BibTeX"):
        service.ingest([UploadPart("paper.pdf", BytesIO(b"%PDF-1.7"))], bibtex=UploadPart("refs.bib", BytesIO(b"bad")))
    assert state.list_batches() == []


def test_bibtex_structural_key_and_type_are_authoritative(tmp_path: Path) -> None:
    service, state, _ = build_ingestor(tmp_path)
    result = service.ingest(
        [UploadPart("paper.pdf", BytesIO(b"%PDF-1.7"))],
        bibtex=UploadPart(
            "refs.bib",
            BytesIO(b"@article{actual, key={forged}, type={forged}, title={Paper}}"),
        ),
    )

    entry = state.list_bibtex_entries(result.batch_id)[0]
    assert entry["key"] == "actual"
    assert entry["type"] == "article"

    service, state, _ = build_ingestor(tmp_path / "large", bibtex_max_bytes=40)
    with pytest.raises(ValueError, match="BibTeX"):
        service.ingest(
            [UploadPart("paper.pdf", BytesIO(b"%PDF-1.7"))],
            bibtex=UploadPart("refs.bib", BytesIO(b"@article{key," + b"x" * 50)),
        )
    assert state.list_batches() == []


def test_rejects_model_outside_configured_allowlist(tmp_path: Path) -> None:
    service, state, _ = build_ingestor(tmp_path)
    with pytest.raises(ValueError, match="allowlist"):
        service.ingest([UploadPart("paper.pdf", BytesIO(b"%PDF-1.7"))], selected_models={"ocr": "other"})
    assert state.list_batches() == []


def test_stream_failure_aborts_partial_artifact_and_abort_is_idempotent(tmp_path: Path) -> None:
    service, state, artifacts = build_ingestor(tmp_path)
    with pytest.raises(OSError, match="stream failed"):
        service.ingest([UploadPart("paper.pdf", cast(BinaryIO, FailingStream()))])
    assert state.list_batches() == []
    assert list((tmp_path / "work").iterdir()) == []

    job = state.create_job()
    pending = artifacts.begin(job, "pdf")
    pending.write(b"%PDF-partial")
    pending.abort()
    pending.abort()
    assert artifacts.resolve(job, "pdf") is None
