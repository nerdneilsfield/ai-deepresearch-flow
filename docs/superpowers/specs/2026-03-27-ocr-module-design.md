# OCR Module Design — Pluggable Backends (PaddleOCR First)

**Date:** 2026-03-27
**Scope:** Phase A — OCR engine layer only. Calls backend, outputs markdown + images to directory. No automatic integration with recognize post-processing pipeline.

## Overview

Add an OCR engine module to `deepresearch_flow` with a pluggable backend system configured via `ocr.toml`. The first backend is PaddleOCR's synchronous cloud API. Output is compatible with the existing `recognize organize --layout mineru` downstream pipeline.

## Module Structure

```
python/deepresearch_flow/ocr/
  __init__.py
  config.py              # Load ocr.toml, dataclass config
  base.py                # OcrBackend protocol + OcrResult/OcrPage dataclasses
  factory.py             # backend type → OcrBackend instance
  runner.py              # Orchestration: read file → call backend → merge pages → write output
  backends/
    __init__.py
    paddle.py            # PaddleOCR sync API implementation
```

## Core Types (`base.py`)

```python
@dataclass(frozen=True)
class OcrPage:
    page_index: int
    markdown: str                    # Markdown text for this page
    images: dict[str, bytes]         # relative_path → image bytes
    missing_images: tuple[str, ...] = ()   # references that failed to download

@dataclass(frozen=True)
class OcrResult:
    pages: list[OcrPage]

class OcrBackend(Protocol):
    def ocr(self, file_path: Path) -> OcrResult: ...
```

- `file_type` (PDF vs image) is inferred from file extension inside each backend implementation, not exposed to callers.
- Image bytes are held in `OcrPage.images`; the runner writes them to disk.

### Image Reference Contract

Backend implementations **must** satisfy these invariants when constructing `OcrPage`:

1. `OcrPage.markdown` references images using keys from `OcrPage.images` for all successfully downloaded images — i.e., `![alt](images/page_0001_fig_01.png)` where the path matches a key in `images`.
2. `OcrPage.images` keys **must** use the format `images/page_{page_index:04d}_{counter}_{kind}.{ext}` (e.g., `images/page_0001_00_figure.png`). The backend is responsible for downloading remote URLs and normalizing references before returning. Names are unique within a document output directory.
3. If an image download fails, the backend **must** still include the normalized reference in `markdown` (so the user sees what's missing) and record the path in `OcrPage.missing_images: list[str]`. The failed image is **not** added to `OcrPage.images`.
4. The runner **only** writes `OcrPage.images` bytes to disk, concatenates `OcrPage.markdown`, and logs any `missing_images` as warnings — it does **not** rewrite, match, or guess image references.

This means all URL → local path normalization happens inside the backend, keeping the runner simple and backend-agnostic.

## Configuration (`ocr.toml`)

```toml
[general]
output_dir = "ocr_output"       # Default output directory

[backend]
type = "paddle"                 # Backend type (dispatched by factory)
api_url = "https://paddleocr.aistudio-app.com/layout-parsing"
token = "env:PADDLE_OCR_TOKEN"  # Supports env: prefix for secret resolution

[backend.options]               # Backend-specific options, passed through
useDocOrientationClassify = false
useDocUnwarping = false
useChartRecognition = false
```

- `env:` prefix reuses the project's existing environment variable resolution pattern.
- `[backend.options]` is optional; different backends may have different fields.

## Factory (`factory.py`)

```python
def create_backend(config: BackendConfig) -> OcrBackend:
    if config.type == "paddle":
        from .backends.paddle import PaddleOcrBackend
        return PaddleOcrBackend(config)
    raise ValueError(f"Unknown OCR backend type: {config.type}")
```

## PaddleOCR Backend (`backends/paddle.py`)

Implements the synchronous layout-parsing API:

1. Read file bytes, base64-encode.
2. Determine `fileType` from extension (`.pdf` → 0, image extensions → 1).
3. POST to `api_url` with `Authorization: token {TOKEN}`, JSON payload.
4. Parse response: iterate `layoutParsingResults`, extract markdown text and image URLs.
5. Download image bytes from URLs returned in the response.
6. Return `OcrResult` with one `OcrPage` per layout parsing result.

## Runner (`runner.py`)

Orchestrates the full OCR flow:

1. Load config from `ocr.toml`.
2. Discover input files (single file or directory of PDFs/images).
3. For each input file:
   a. Call `backend.ocr(file_path)` → `OcrResult`.
   b. Merge all pages into a single `full.md` with `\n\n---\n\n` page separators (markdown is used as-is from backend, no rewriting).
   c. Write all `OcrPage.images` bytes to disk under `output_dir/<dir_name>/`.
   d. Write `full.md` to `output_dir/<dir_name>/`.
4. Log progress per file.

### Output Directory Naming

- Default: `<dir_name>` = file stem (e.g., `paper.pdf` → `paper/`).
- **Collision handling:** If `output_dir/<stem>/` already exists, append `_<N>` (e.g., `paper_1/`, `paper_2/`). This handles `a/paper.pdf` and `b/paper.pdf` processed in the same batch.
- Image filenames are globally unique by construction (see Image Reference Contract above: `page_{page_index:04d}_{counter}_{kind}.{ext}`), so no image-level collision handling is needed.

## Output Structure

```
ocr_output/
  paper1/
    full.md              # All pages merged, images referenced as ![alt](images/page_0000_00_figure.png)
    images/
      page_0000_00_figure.png
      page_0001_00_table.png
  paper2/
    full.md
    images/
      ...
```

Compatible with `deepresearch-flow recognize organize --input <dir> --layout mineru`.

## CLI Entry Point

Added as a subcommand under `recognize`:

```
deepresearch-flow recognize ocr <file_or_dir> [--config ocr.toml] [--output-dir ./output]
```

- `<file_or_dir>`: Single PDF/image file, or directory containing multiple files.
- `--config`: Path to `ocr.toml`. Default: `ocr.toml` in current directory.
- `--output-dir`: Override `general.output_dir` from config.
- Supported file extensions: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.bmp`, `.webp`.

## Error Handling

- Missing/invalid config file → clear error message, exit 1.
- Backend API errors (non-200 status, network failure) → log error with file name, skip file, continue with remaining files. Print summary of failures at end.
- **Image download failure** (OCR request succeeded but individual image URL returns 404/timeout) → backend logs the failure with source URL and page index at download time, then records the local ref path in `OcrPage.missing_images`. Runner logs a warning per missing local ref. `full.md` retains the reference so the user can see what's missing and re-run or manually fix.
- Unsupported file extension → warn and skip.
- Empty OCR result (no pages) → warn and skip.

## Sync/Async Boundary

Phase A uses **synchronous** `httpx` calls only. No async, no concurrency. Files are processed serially one at a time. This is intentional — keeps the implementation simple and avoids the complexity of async error handling. Concurrent/async processing is deferred to future phases.

## Dependencies

- `httpx` — already available in the project (used by push module and LLM providers).
- No new dependencies required.

## Testing Strategy

- Unit tests for config loading (valid TOML, env: resolution, missing fields).
- Unit tests for PaddleOCR backend with mocked HTTP responses.
- Unit tests for runner (page merge, output directory naming/collision, image writing, missing_images warnings).
- Integration test with a small mock server if feasible.

## Future Extensions (Out of Scope)

- Phase B: Auto-chain with `recognize` post-processing pipeline.
- Phase C: Feed OCR output into `paper extract` pipeline.
- Additional backends (local PaddleOCR SDK, Tesseract, Azure Document AI, etc.).
- Async/batch processing for large file sets.
