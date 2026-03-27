"""Core OCR types and backend protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class OcrPage:
    """One page of OCR output.

    Image Reference Contract:
    - ``markdown`` references images using keys from ``images``.
    - ``images`` keys use format ``images/page_{page_index:04d}_{counter}_{kind}.{ext}``.
    - Failed downloads go into ``missing_images``, not ``images``.
    """

    page_index: int
    markdown: str
    images: dict[str, bytes]
    missing_images: tuple[str, ...] = ()


@dataclass(frozen=True)
class OcrResult:
    """Aggregated OCR output for a single input file."""

    pages: list[OcrPage]


class OcrBackend(Protocol):
    """Protocol that every OCR backend must satisfy."""

    def ocr(self, file_path: Path) -> OcrResult: ...
