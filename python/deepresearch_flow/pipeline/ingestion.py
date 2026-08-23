"""Streaming upload ingestion for the optional administrative pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import BinaryIO, Iterable

from pybtex.database import parse_string

from .artifacts import ArtifactStore
from .config import PipelineConfig
from .state import PipelineState


@dataclass(frozen=True)
class UploadPart:
    filename: str
    stream: BinaryIO


@dataclass(frozen=True)
class IngestionResult:
    batch_id: str
    jobs: tuple[str, ...]
    bibtex_status: str


class BatchIngestor:
    def __init__(self, config: PipelineConfig, state: PipelineState, artifacts: ArtifactStore):
        self.config = config
        self.state = state
        self.artifacts = artifacts

    def ingest(
        self,
        pdfs: Iterable[UploadPart],
        *,
        bibtex: UploadPart | None = None,
        selected_models: dict[str, str] | None = None,
    ) -> IngestionResult:
        models = self._validate_models(selected_models)
        batch_id = self.state.create_batch()
        jobs: list[str] = []
        seen: set[str] = set()
        total = 0
        pending = None
        try:
            for index, part in enumerate(pdfs):
                if index >= self.config.pdfs_per_batch:
                    raise ValueError("PDF count exceeds limit")
                self._validate_pdf_name(part.filename)
                job_id = self.state.create_job(batch_id, selected_models=models, config_fingerprint=self.config.fingerprint())
                jobs.append(job_id)
                pending = self.artifacts.begin(job_id, "pdf")
                prefix = bytearray()
                size = 0
                digest = hashlib.sha256()
                while True:
                    chunk = part.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise ValueError("PDF stream must return bytes")
                    if len(prefix) < 5:
                        prefix.extend(chunk[: 5 - len(prefix)])
                    size += len(chunk)
                    total += len(chunk)
                    if size > self.config.max_pdf_bytes:
                        raise ValueError("PDF size exceeds limit")
                    if total > self.config.max_batch_bytes:
                        raise ValueError("batch size exceeds limit")
                    digest.update(chunk)
                    pending.write(chunk)
                if size == 0:
                    raise ValueError("PDF is empty")
                if bytes(prefix) != b"%PDF-":
                    raise ValueError("PDF header is invalid")
                if digest.hexdigest() in seen:
                    raise ValueError("duplicate PDF content")
                seen.add(digest.hexdigest())
                pending.promote()
                pending = None
                self.state.set_job_input(job_id, part.filename, digest.hexdigest(), size)
            if not jobs:
                raise ValueError("PDF count must be positive")
            entries: list[dict[str, object]] = []
            bibtex_status = "not_provided"
            if bibtex is not None:
                entries = self._read_bibtex(bibtex)
                self.state.persist_bibtex_entries(batch_id, entries)
                bibtex_status = "provided"
            return IngestionResult(batch_id, tuple(jobs), bibtex_status)
        except Exception:
            if pending is not None:
                pending.abort()
            for job_id in jobs:
                self.artifacts.discard_job(job_id)
            self.state.discard_batch(batch_id)
            raise

    def _validate_models(self, selected: dict[str, str] | None) -> dict[str, str]:
        selected = dict(selected or {})
        groups = {"ocr": self.config.ocr, "extract": self.config.extract, "translate": self.config.translate}
        unknown = set(selected) - set(groups)
        if unknown:
            raise ValueError("model selection outside allowlist")
        resolved: dict[str, str] = {}
        for name, group in groups.items():
            value = selected.get(name, group.default)
            if value is None or value not in group.allowlist:
                raise ValueError(f"{name} model is outside allowlist")
            resolved[name] = value
        return resolved

    @staticmethod
    def _validate_pdf_name(filename: str) -> None:
        if not isinstance(filename, str) or not filename.lower().endswith(".pdf"):
            raise ValueError("PDF extension is invalid")

    def _read_bibtex(self, part: UploadPart) -> list[dict[str, object]]:
        if not isinstance(part.filename, str) or not part.filename.lower().endswith(".bib"):
            raise ValueError("BibTeX extension is invalid")
        buffer = bytearray()
        while True:
            chunk = part.stream.read(1024 * 1024)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise ValueError("BibTeX stream must return bytes")
            buffer.extend(chunk)
            if len(buffer) > self.config.bibtex_max_bytes:
                raise ValueError("BibTeX size exceeds limit")
        try:
            database = parse_string(bytes(buffer).decode("utf-8"), bib_format="bibtex")
        except Exception as exc:
            raise ValueError("BibTeX syntax is invalid") from exc
        result: list[dict[str, object]] = []
        for key, entry in database.entries.items():
            fields = {str(name).lower(): str(value) for name, value in entry.fields.items()}
            authors = entry.persons.get("author", ())
            if authors:
                fields["author"] = ", ".join(str(person) for person in authors)
            result.append({**fields, "fields": fields, "key": key, "type": entry.type})
        if not result:
            raise ValueError("BibTeX syntax is invalid")
        return result


# Descriptive aliases keep the service seam discoverable to callers.
IngestionService = BatchIngestor
UploadIngestion = BatchIngestor
