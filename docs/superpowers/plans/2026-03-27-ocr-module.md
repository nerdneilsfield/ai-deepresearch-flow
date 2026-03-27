# OCR Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable OCR engine module with PaddleOCR backend that outputs mineru-compatible markdown + images.

**Architecture:** New `ocr/` package under `deepresearch_flow` with Protocol-based backend abstraction. Config via standalone `ocr.toml`. CLI registered as `recognize ocr` subcommand. Synchronous httpx, serial file processing.

**Tech Stack:** Python 3.12+, httpx (sync), click, tomllib, dataclasses, typing.Protocol

**Spec:** `docs/superpowers/specs/2026-03-27-ocr-module-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/ocr/__init__.py` | Create | Package init |
| `python/deepresearch_flow/ocr/base.py` | Create | OcrPage, OcrResult, OcrBackend protocol |
| `python/deepresearch_flow/ocr/config.py` | Create | Load ocr.toml, dataclasses, env: resolution |
| `python/deepresearch_flow/ocr/factory.py` | Create | Backend type → instance dispatcher |
| `python/deepresearch_flow/ocr/runner.py` | Create | Orchestration: discover files → call backend → merge pages → write output |
| `python/deepresearch_flow/ocr/backends/__init__.py` | Create | Backends package init |
| `python/deepresearch_flow/ocr/backends/paddle.py` | Create | PaddleOCR sync API implementation |
| `python/deepresearch_flow/recognize/cli.py` | Modify | Add `ocr` subcommand under `recognize` group |
| `python/deepresearch_flow/ocr/tests/__init__.py` | Create | Tests package init |
| `python/deepresearch_flow/ocr/tests/test_base.py` | Create | Tests for base types |
| `python/deepresearch_flow/ocr/tests/test_config.py` | Create | Tests for config loading |
| `python/deepresearch_flow/ocr/tests/test_paddle.py` | Create | Tests for PaddleOCR backend (mocked HTTP) |
| `python/deepresearch_flow/ocr/tests/test_runner.py` | Create | Tests for runner orchestration |
| `python/deepresearch_flow/ocr/tests/test_factory.py` | Create | Tests for factory dispatch |
| `ocr.example.toml` | Create | Example config file |

---

## Task 1: Base Types (`base.py`)

**Files:**
- Create: `python/deepresearch_flow/ocr/__init__.py`
- Create: `python/deepresearch_flow/ocr/base.py`
- Create: `python/deepresearch_flow/ocr/backends/__init__.py`
- Create: `python/deepresearch_flow/ocr/tests/__init__.py`
- Test: `python/deepresearch_flow/ocr/tests/test_base.py`

- [ ] **Step 1: Write the failing tests for base types**

```python
"""Tests for OCR base types."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.ocr.base import OcrBackend, OcrPage, OcrResult


class TestOcrPage:
    def test_frozen(self) -> None:
        page = OcrPage(page_index=0, markdown="hello", images={})
        with pytest.raises(AttributeError):
            page.markdown = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        page = OcrPage(page_index=0, markdown="hello", images={})
        assert page.missing_images == ()

    def test_with_images_and_missing(self) -> None:
        page = OcrPage(
            page_index=1,
            markdown="![fig](images/page_0001_00_figure.png)",
            images={"images/page_0001_00_figure.png": b"\x89PNG"},
            missing_images=("images/page_0001_01_table.png",),
        )
        assert len(page.images) == 1
        assert len(page.missing_images) == 1


class TestOcrResult:
    def test_empty_pages(self) -> None:
        result = OcrResult(pages=[])
        assert result.pages == []

    def test_multiple_pages(self) -> None:
        pages = [
            OcrPage(page_index=0, markdown="page0", images={}),
            OcrPage(page_index=1, markdown="page1", images={}),
        ]
        result = OcrResult(pages=pages)
        assert len(result.pages) == 2


class TestOcrBackendProtocol:
    def test_protocol_compliance(self) -> None:
        """A class with an ocr(Path) -> OcrResult method satisfies the protocol."""

        class FakeBackend:
            def ocr(self, file_path: Path) -> OcrResult:
                return OcrResult(pages=[])

        backend: OcrBackend = FakeBackend()
        result = backend.ocr(Path("test.pdf"))
        assert result.pages == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deepresearch_flow.ocr'`

- [ ] **Step 3: Create package structure and implement base types**

`python/deepresearch_flow/ocr/__init__.py`:
```python
"""OCR engine module with pluggable backends."""
```

`python/deepresearch_flow/ocr/backends/__init__.py`:
```python
"""OCR backend implementations."""
```

`python/deepresearch_flow/ocr/tests/__init__.py`:
```python
"""OCR module tests."""
```

`python/deepresearch_flow/ocr/base.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_base.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/ocr/__init__.py python/deepresearch_flow/ocr/base.py python/deepresearch_flow/ocr/backends/__init__.py python/deepresearch_flow/ocr/tests/__init__.py python/deepresearch_flow/ocr/tests/test_base.py
git commit -m "feat(ocr): add base types — OcrPage, OcrResult, OcrBackend protocol"
```

---

## Task 2: Config Loading (`config.py`)

**Files:**
- Create: `python/deepresearch_flow/ocr/config.py`
- Create: `ocr.example.toml`
- Test: `python/deepresearch_flow/ocr/tests/test_config.py`

- [ ] **Step 1: Write the failing tests for config loading**

```python
"""Tests for OCR config loading."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from deepresearch_flow.ocr.config import (
    BackendConfig,
    GeneralConfig,
    OcrConfig,
    load_ocr_config,
)


@pytest.fixture()
def valid_toml(tmp_path: Path) -> Path:
    p = tmp_path / "ocr.toml"
    p.write_text(
        textwrap.dedent("""\
            [general]
            output_dir = "my_output"

            [backend]
            type = "paddle"
            api_url = "https://example.com/api"
            token = "test-token-123"

            [backend.options]
            useDocOrientationClassify = false
        """)
    )
    return p


@pytest.fixture()
def env_toml(tmp_path: Path) -> Path:
    p = tmp_path / "ocr.toml"
    p.write_text(
        textwrap.dedent("""\
            [general]
            output_dir = "out"

            [backend]
            type = "paddle"
            api_url = "https://example.com/api"
            token = "env:TEST_OCR_TOKEN"
        """)
    )
    return p


class TestLoadOcrConfig:
    def test_valid_config(self, valid_toml: Path) -> None:
        cfg = load_ocr_config(valid_toml)
        assert isinstance(cfg, OcrConfig)
        assert cfg.general.output_dir == "my_output"
        assert cfg.backend.type == "paddle"
        assert cfg.backend.api_url == "https://example.com/api"
        assert cfg.backend.token == "test-token-123"
        assert cfg.backend.options == {"useDocOrientationClassify": False}

    def test_env_prefix_resolution(self, env_toml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_OCR_TOKEN", "resolved-secret")
        cfg = load_ocr_config(env_toml)
        assert cfg.backend.token == "resolved-secret"

    def test_env_prefix_missing_raises(self, env_toml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_OCR_TOKEN", raising=False)
        with pytest.raises(ValueError, match="TEST_OCR_TOKEN"):
            load_ocr_config(env_toml)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_ocr_config(tmp_path / "nonexistent.toml")

    def test_missing_backend_section_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "ocr.toml"
        p.write_text("[general]\noutput_dir = 'out'\n")
        with pytest.raises(ValueError, match="backend"):
            load_ocr_config(p)

    def test_missing_backend_type_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "ocr.toml"
        p.write_text(
            textwrap.dedent("""\
                [general]
                output_dir = "out"

                [backend]
                api_url = "https://example.com/api"
                token = "tok"
            """)
        )
        with pytest.raises(ValueError, match="type"):
            load_ocr_config(p)

    def test_missing_api_url_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "ocr.toml"
        p.write_text(
            textwrap.dedent("""\
                [backend]
                type = "paddle"
                token = "tok"
            """)
        )
        with pytest.raises(ValueError, match="api_url"):
            load_ocr_config(p)

    def test_missing_token_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "ocr.toml"
        p.write_text(
            textwrap.dedent("""\
                [backend]
                type = "paddle"
                api_url = "https://example.com/api"
            """)
        )
        with pytest.raises(ValueError, match="token"):
            load_ocr_config(p)

    def test_default_output_dir(self, tmp_path: Path) -> None:
        p = tmp_path / "ocr.toml"
        p.write_text(
            textwrap.dedent("""\
                [backend]
                type = "paddle"
                api_url = "https://example.com/api"
                token = "tok"
            """)
        )
        cfg = load_ocr_config(p)
        assert cfg.general.output_dir == "ocr_output"

    def test_empty_options(self, tmp_path: Path) -> None:
        p = tmp_path / "ocr.toml"
        p.write_text(
            textwrap.dedent("""\
                [backend]
                type = "paddle"
                api_url = "https://example.com/api"
                token = "tok"
            """)
        )
        cfg = load_ocr_config(p)
        assert cfg.backend.options == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deepresearch_flow.ocr.config'`

- [ ] **Step 3: Implement config loading**

`python/deepresearch_flow/ocr/config.py`:
```python
"""OCR configuration loading from ocr.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GeneralConfig:
    output_dir: str = "ocr_output"


@dataclass(frozen=True)
class BackendConfig:
    type: str
    api_url: str
    token: str
    options: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class OcrConfig:
    general: GeneralConfig
    backend: BackendConfig


def _resolve_env(value: str) -> str:
    """Resolve ``env:VAR_NAME`` to the environment variable value."""
    if not value.startswith("env:"):
        return value
    env_name = value.split(":", 1)[1]
    resolved = os.environ.get(env_name)
    if not resolved:
        raise ValueError(
            f"Environment variable '{env_name}' is not set "
            f"(referenced as 'env:{env_name}' in ocr.toml)"
        )
    return resolved


def load_ocr_config(path: Path) -> OcrConfig:
    """Load and validate OCR configuration from a TOML file."""
    if not path.exists():
        raise FileNotFoundError(f"OCR config file not found: {path}")

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    # General section (optional, has defaults).
    general_raw = raw.get("general", {})
    general = GeneralConfig(
        output_dir=general_raw.get("output_dir", "ocr_output"),
    )

    # Backend section (required).
    backend_raw = raw.get("backend")
    if not backend_raw:
        raise ValueError("'[backend]' section is required in ocr.toml")

    backend_type = backend_raw.get("type")
    if not backend_type:
        raise ValueError("'type' is required in [backend] section of ocr.toml")

    api_url = backend_raw.get("api_url", "")
    if not api_url:
        raise ValueError("'api_url' is required in [backend] section of ocr.toml")

    token_raw = backend_raw.get("token", "")
    if not token_raw:
        raise ValueError("'token' is required in [backend] section of ocr.toml")
    token = _resolve_env(token_raw)

    options = backend_raw.get("options", {})

    backend = BackendConfig(
        type=backend_type,
        api_url=api_url,
        token=token,
        options=options,
    )

    return OcrConfig(general=general, backend=backend)
```

- [ ] **Step 4: Create example config file**

`ocr.example.toml` (project root):
```toml
# OCR Configuration
# Copy to ocr.toml and fill in your values.

[general]
output_dir = "ocr_output"       # Default output directory

[backend]
type = "paddle"                 # Backend type: "paddle"
api_url = "https://paddleocr.aistudio-app.com/layout-parsing"
token = "env:PADDLE_OCR_TOKEN"  # Supports env: prefix for secret resolution

[backend.options]               # Backend-specific options (optional)
useDocOrientationClassify = false
useDocUnwarping = false
useChartRecognition = false
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_config.py -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/ocr/config.py python/deepresearch_flow/ocr/tests/test_config.py ocr.example.toml
git commit -m "feat(ocr): add config loading with env: prefix resolution"
```

---

## Task 3: Factory (`factory.py`)

**Files:**
- Create: `python/deepresearch_flow/ocr/factory.py`
- Test: `python/deepresearch_flow/ocr/tests/test_factory.py`

- [ ] **Step 1: Write the failing tests for factory**

```python
"""Tests for OCR backend factory."""

from __future__ import annotations

import pytest

from deepresearch_flow.ocr.config import BackendConfig
from deepresearch_flow.ocr.factory import create_backend


class TestCreateBackend:
    def test_unknown_type_raises(self) -> None:
        cfg = BackendConfig(type="unknown", api_url="http://x", token="t")
        with pytest.raises(ValueError, match="Unknown OCR backend type: unknown"):
            create_backend(cfg)

    def test_paddle_returns_backend(self) -> None:
        cfg = BackendConfig(
            type="paddle",
            api_url="https://example.com/api",
            token="test-token",
            options={"useDocOrientationClassify": False},
        )
        backend = create_backend(cfg)
        # Verify it has the ocr method (Protocol compliance).
        assert callable(getattr(backend, "ocr", None))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deepresearch_flow.ocr.factory'`

- [ ] **Step 3: Implement factory**

`python/deepresearch_flow/ocr/factory.py`:
```python
"""Factory for creating OCR backend instances from config."""

from __future__ import annotations

from deepresearch_flow.ocr.base import OcrBackend
from deepresearch_flow.ocr.config import BackendConfig


def create_backend(config: BackendConfig) -> OcrBackend:
    """Create an OCR backend instance based on the config type."""
    if config.type == "paddle":
        from deepresearch_flow.ocr.backends.paddle import PaddleOcrBackend

        return PaddleOcrBackend(config)

    raise ValueError(f"Unknown OCR backend type: {config.type}")
```

Note: This will fail the `test_paddle_returns_backend` test until Task 4 creates the PaddleOcrBackend. That's expected — we commit the factory now and the paddle test goes green in Task 4.

- [ ] **Step 4: Run tests to verify `test_unknown_type_raises` passes**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_factory.py::TestCreateBackend::test_unknown_type_raises -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/ocr/factory.py python/deepresearch_flow/ocr/tests/test_factory.py
git commit -m "feat(ocr): add backend factory dispatcher"
```

---

## Task 4: PaddleOCR Backend (`backends/paddle.py`)

**Files:**
- Create: `python/deepresearch_flow/ocr/backends/paddle.py`
- Test: `python/deepresearch_flow/ocr/tests/test_paddle.py`

- [ ] **Step 1: Write the failing tests for PaddleOCR backend**

```python
"""Tests for PaddleOCR backend with mocked HTTP responses."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from deepresearch_flow.ocr.backends.paddle import PaddleOcrBackend
from deepresearch_flow.ocr.config import BackendConfig

# --- Fixtures ----------------------------------------------------------------

FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n fake png data"


@pytest.fixture()
def backend() -> PaddleOcrBackend:
    cfg = BackendConfig(
        type="paddle",
        api_url="https://example.com/layout-parsing",
        token="test-token",
        options={"useDocOrientationClassify": False},
    )
    return PaddleOcrBackend(cfg)


@pytest.fixture()
def single_page_response() -> dict:
    """Minimal PaddleOCR API response with one page."""
    return {
        "result": {
            "layoutParsingResults": [
                {
                    "markdown": {
                        "text": "# Title\n\nSome text\n\n![fig](http://cdn.example.com/fig1.png)",
                        "images": {
                            "fig1.png": "http://cdn.example.com/fig1.png",
                        },
                    },
                    "outputImages": {
                        "layout": "http://cdn.example.com/layout_0.jpg",
                    },
                }
            ]
        }
    }


@pytest.fixture()
def multi_page_response() -> dict:
    """PaddleOCR API response with two pages."""
    return {
        "result": {
            "layoutParsingResults": [
                {
                    "markdown": {
                        "text": "Page 0 text",
                        "images": {},
                    },
                    "outputImages": {},
                },
                {
                    "markdown": {
                        "text": "Page 1 text with ![img](http://cdn.example.com/t.png)",
                        "images": {"t.png": "http://cdn.example.com/t.png"},
                    },
                    "outputImages": {},
                },
            ]
        }
    }


# --- Helpers ------------------------------------------------------------------


def _mock_transport(ocr_response: dict, image_bytes: bytes = FAKE_IMAGE_BYTES) -> httpx.MockTransport:
    """Build a MockTransport that returns the OCR response for POST and image bytes for GET."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=ocr_response)
        # GET requests are image downloads.
        return httpx.Response(200, content=image_bytes)

    return httpx.MockTransport(handler)


def _mock_transport_with_image_failure(ocr_response: dict) -> httpx.MockTransport:
    """POST succeeds, but all GET (image download) requests return 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=ocr_response)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# --- Tests --------------------------------------------------------------------


class TestPaddleOcrBackend:
    def test_single_page_pdf(
        self, backend: PaddleOcrBackend, single_page_response: dict, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        transport = _mock_transport(single_page_response)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            result = backend.ocr(pdf_file)

        assert len(result.pages) == 1
        page = result.pages[0]
        assert page.page_index == 0
        # Markdown references should be rewritten to local paths.
        assert "images/page_0000_" in page.markdown
        assert "http://cdn.example.com" not in page.markdown
        # Images dict should have the downloaded bytes.
        assert len(page.images) == 2  # fig + layout output image
        for key, data in page.images.items():
            assert key.startswith("images/page_0000_")
            assert data == FAKE_IMAGE_BYTES
        assert page.missing_images == ()

    def test_multi_page(
        self, backend: PaddleOcrBackend, multi_page_response: dict, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "multi.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        transport = _mock_transport(multi_page_response)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            result = backend.ocr(pdf_file)

        assert len(result.pages) == 2
        assert result.pages[0].page_index == 0
        assert result.pages[1].page_index == 1
        # Page 1 has one markdown image.
        assert len(result.pages[1].images) == 1

    def test_image_file_type(self, backend: PaddleOcrBackend, tmp_path: Path) -> None:
        """Image files should set fileType=1 in the API request."""
        img_file = tmp_path / "scan.png"
        img_file.write_bytes(b"\x89PNG fake")

        captured_request: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                captured_request.append(request)
                return httpx.Response(
                    200,
                    json={"result": {"layoutParsingResults": [{"markdown": {"text": "ok", "images": {}}, "outputImages": {}}]}},
                )
            return httpx.Response(200, content=b"img")

        transport = httpx.MockTransport(handler)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            backend.ocr(img_file)

        body = json.loads(captured_request[0].content)
        assert body["fileType"] == 1

    def test_api_error_raises(self, backend: PaddleOcrBackend, tmp_path: Path) -> None:
        pdf_file = tmp_path / "bad.pdf"
        pdf_file.write_bytes(b"%PDF")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        transport = httpx.MockTransport(handler)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            with pytest.raises(httpx.HTTPStatusError):
                backend.ocr(pdf_file)

    def test_image_download_failure_records_missing(
        self, backend: PaddleOcrBackend, single_page_response: dict, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        transport = _mock_transport_with_image_failure(single_page_response)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            result = backend.ocr(pdf_file)

        page = result.pages[0]
        # Images dict should be empty (all downloads failed).
        assert len(page.images) == 0
        # Missing images should be recorded.
        assert len(page.missing_images) == 2  # fig + layout output image
        # Markdown still has the local references (not the original URLs).
        assert "images/page_0000_" in page.markdown

    def test_unsupported_extension_raises(self, backend: PaddleOcrBackend, tmp_path: Path) -> None:
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("hello")

        with pytest.raises(ValueError, match="Unsupported file extension"):
            backend.ocr(txt_file)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_paddle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deepresearch_flow.ocr.backends.paddle'`

- [ ] **Step 3: Implement PaddleOCR backend**

`python/deepresearch_flow/ocr/backends/paddle.py`:
```python
"""PaddleOCR synchronous cloud API backend."""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

import httpx

from deepresearch_flow.ocr.base import OcrPage, OcrResult
from deepresearch_flow.ocr.config import BackendConfig

logger = logging.getLogger(__name__)

_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
_SUPPORTED_EXTENSIONS = _PDF_EXTENSIONS | _IMAGE_EXTENSIONS

# Matches markdown image references: ![alt](url)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _file_type_for(path: Path) -> int:
    """Return PaddleOCR fileType: 0 for PDF, 1 for images."""
    ext = path.suffix.lower()
    if ext in _PDF_EXTENSIONS:
        return 0
    if ext in _IMAGE_EXTENSIONS:
        return 1
    raise ValueError(f"Unsupported file extension: {ext}")


def _image_ext_from_url(url: str) -> str:
    """Extract file extension from a URL, defaulting to .png."""
    # Strip query params.
    clean = url.split("?")[0]
    ext = Path(clean).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"):
        return ext
    return ".png"


class PaddleOcrBackend:
    """PaddleOCR layout-parsing synchronous API backend."""

    def __init__(self, config: BackendConfig) -> None:
        self._api_url = config.api_url
        self._token = config.token
        self._options = dict(config.options)
        self._client = httpx.Client(timeout=120.0)

    def ocr(self, file_path: Path) -> OcrResult:
        """Run OCR on a file and return structured results."""
        file_type = _file_type_for(file_path)
        file_data = base64.b64encode(file_path.read_bytes()).decode("ascii")

        payload: dict[str, object] = {
            "file": file_data,
            "fileType": file_type,
        }
        if self._options:
            payload["optionalPayload"] = self._options

        headers = {
            "Authorization": f"token {self._token}",
            "Content-Type": "application/json",
        }

        resp = self._client.post(self._api_url, json=payload, headers=headers)
        resp.raise_for_status()

        data = resp.json()
        layout_results = data["result"]["layoutParsingResults"]

        pages: list[OcrPage] = []
        for page_idx, layout in enumerate(layout_results):
            page = self._process_page(page_idx, layout)
            pages.append(page)

        return OcrResult(pages=pages)

    def _process_page(self, page_idx: int, layout: dict) -> OcrPage:
        """Process a single layoutParsingResult into an OcrPage."""
        md_section = layout["markdown"]
        raw_markdown: str = md_section["text"]
        raw_images: dict[str, str] = md_section.get("images", {})
        output_images: dict[str, str] = layout.get("outputImages", {})

        # Build mapping: original_ref → (local_key, url)
        # Counter tracks images within this page.
        counter = 0
        url_to_local: dict[str, str] = {}
        images: dict[str, bytes] = {}
        missing: list[str] = []

        # Process markdown images.
        for _name, url in raw_images.items():
            ext = _image_ext_from_url(url)
            local_key = f"images/page_{page_idx:04d}_{counter:02d}_md{ext}"
            counter += 1
            url_to_local[url] = local_key
            self._download_image(url, local_key, images, missing)

        # Process output images (layout visualizations etc.).
        for kind, url in output_images.items():
            ext = _image_ext_from_url(url)
            local_key = f"images/page_{page_idx:04d}_{counter:02d}_{kind}{ext}"
            counter += 1
            url_to_local[url] = local_key
            self._download_image(url, local_key, images, missing)

        # Rewrite markdown: replace all image URLs with local keys.
        def _replace_ref(match: re.Match[str]) -> str:
            alt = match.group(1)
            ref = match.group(2)
            local = url_to_local.get(ref, ref)
            return f"![{alt}]({local})"

        normalized_markdown = _IMAGE_RE.sub(_replace_ref, raw_markdown)

        return OcrPage(
            page_index=page_idx,
            markdown=normalized_markdown,
            images=images,
            missing_images=tuple(missing),
        )

    def _download_image(
        self,
        url: str,
        local_key: str,
        images: dict[str, bytes],
        missing: list[str],
    ) -> None:
        """Download an image URL. On success, add to images; on failure, add to missing."""
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            images[local_key] = resp.content
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Failed to download image %s: %s", url, exc)
            missing.append(local_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_paddle.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Also verify factory test now passes**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_factory.py -v`
Expected: All 2 tests PASS (including `test_paddle_returns_backend`)

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/ocr/backends/paddle.py python/deepresearch_flow/ocr/tests/test_paddle.py
git commit -m "feat(ocr): implement PaddleOCR sync backend with image contract"
```

---

## Task 5: Runner (`runner.py`)

**Files:**
- Create: `python/deepresearch_flow/ocr/runner.py`
- Test: `python/deepresearch_flow/ocr/tests/test_runner.py`

- [ ] **Step 1: Write the failing tests for runner**

```python
"""Tests for OCR runner orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.ocr.base import OcrBackend, OcrPage, OcrResult
from deepresearch_flow.ocr.runner import (
    _merge_pages,
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

        stats = run_ocr(backend, pdf, output_dir)

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
        stats = run_ocr(backend, input_dir, output_dir)

        assert stats["processed"] == 2
        assert (output_dir / "a" / "full.md").exists()
        assert (output_dir / "b" / "full.md").exists()

    def test_empty_result_skipped(self, tmp_path: Path) -> None:
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"%PDF")
        output_dir = tmp_path / "output"

        backend = FakeBackend([])  # No pages.
        stats = run_ocr(backend, pdf, output_dir)

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_runner.py -v`
Expected: FAIL — `ImportError: cannot import name '_merge_pages' from 'deepresearch_flow.ocr.runner'`

- [ ] **Step 3: Implement runner**

`python/deepresearch_flow/ocr/runner.py`:
```python
"""OCR runner — orchestrates file discovery, backend calls, and output writing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from deepresearch_flow.ocr.base import OcrBackend, OcrPage

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
_PAGE_SEPARATOR = "\n\n---\n\n"


class OcrStats(TypedDict):
    processed: int
    failed: int
    skipped: int


def discover_files(path: Path) -> list[Path]:
    """Discover OCR-able files from a path (file or directory)."""
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    if path.is_file():
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension: {path.suffix}. "
                f"Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
            )
        return [path]

    # Directory: collect all supported files.
    files = sorted(
        f
        for f in path.iterdir()
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
    )
    return files


def _merge_pages(
    pages: list[OcrPage],
) -> tuple[str, dict[str, bytes], list[str]]:
    """Merge multiple OcrPages into a single markdown string, combined images dict, and missing list."""
    markdown_parts: list[str] = []
    all_images: dict[str, bytes] = {}
    all_missing: list[str] = []

    for page in pages:
        markdown_parts.append(page.markdown)
        all_images.update(page.images)
        all_missing.extend(page.missing_images)

    merged_md = _PAGE_SEPARATOR.join(markdown_parts)
    return merged_md, all_images, all_missing


def _resolve_output_dir(base: Path, stem: str) -> Path:
    """Resolve output directory, appending _N suffix on collision."""
    candidate = base / stem
    if not candidate.exists():
        return candidate

    n = 1
    while True:
        candidate = base / f"{stem}_{n}"
        if not candidate.exists():
            return candidate
        n += 1


def _write_output(
    output_dir: Path,
    markdown: str,
    images: dict[str, bytes],
    missing: list[str],
) -> None:
    """Write merged markdown and images to the output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write full.md.
    (output_dir / "full.md").write_text(markdown, encoding="utf-8")

    # Write images.
    for rel_path, data in images.items():
        img_path = output_dir / rel_path
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(data)

    # Log missing images.
    for path in missing:
        logger.warning("Missing image in output %s: %s", output_dir.name, path)


def run_ocr(
    backend: OcrBackend,
    input_path: Path,
    output_dir: Path,
) -> OcrStats:
    """Run OCR on input file(s) and write results to output_dir."""
    files = discover_files(input_path)
    stats: OcrStats = {"processed": 0, "failed": 0, "skipped": 0}

    for file_path in files:
        logger.info("Processing: %s", file_path.name)
        try:
            result = backend.ocr(file_path)
        except Exception:
            logger.exception("Failed to OCR %s", file_path.name)
            stats["failed"] += 1
            continue

        if not result.pages:
            logger.warning("Empty OCR result for %s, skipping", file_path.name)
            stats["skipped"] += 1
            continue

        doc_dir = _resolve_output_dir(output_dir, file_path.stem)
        markdown, images, missing = _merge_pages(result.pages)
        _write_output(doc_dir, markdown, images, missing)

        stats["processed"] += 1
        logger.info("Written: %s/full.md (%d pages)", doc_dir.name, len(result.pages))

    return stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_runner.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/ocr/runner.py python/deepresearch_flow/ocr/tests/test_runner.py
git commit -m "feat(ocr): add runner with file discovery, page merge, and output writing"
```

---

## Task 6: CLI Entry Point

**Files:**
- Modify: `python/deepresearch_flow/recognize/cli.py` (add `ocr` subcommand after the `recognize` group at ~line 446)
- No separate test file — tested via `click.testing.CliRunner` in existing test structure or inline test.
- Test: `python/deepresearch_flow/ocr/tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI test**

```python
"""Tests for the OCR CLI subcommand."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from deepresearch_flow.recognize.cli import recognize


@patch("deepresearch_flow.ocr.runner.run_ocr")
@patch("deepresearch_flow.ocr.factory.create_backend")
@patch("deepresearch_flow.ocr.config.load_ocr_config")
class TestOcrCommand:
    def test_missing_config_shows_error(
        self, mock_load: object, mock_factory: object, mock_run: object, tmp_path: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            recognize,
            ["ocr", str(tmp_path / "nonexistent.pdf"), "--config", str(tmp_path / "no.toml")],
        )
        assert result.exit_code != 0

    def test_successful_run(
        self, mock_load: object, mock_factory: object, mock_run: object, tmp_path: Path
    ) -> None:
        # Setup.
        config_path = tmp_path / "ocr.toml"
        config_path.write_text(
            textwrap.dedent("""\
                [backend]
                type = "paddle"
                api_url = "https://example.com/api"
                token = "tok"
            """)
        )
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF")

        from deepresearch_flow.ocr.config import BackendConfig, GeneralConfig, OcrConfig

        mock_load.return_value = OcrConfig(
            general=GeneralConfig(output_dir=str(tmp_path / "output")),
            backend=BackendConfig(type="paddle", api_url="https://x", token="t"),
        )
        mock_run.return_value = {"processed": 1, "failed": 0, "skipped": 0}

        runner = CliRunner()
        result = runner.invoke(
            recognize,
            ["ocr", str(pdf), "--config", str(config_path)],
        )
        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_output_dir_override(
        self, mock_load: object, mock_factory: object, mock_run: object, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "ocr.toml"
        config_path.write_text(
            textwrap.dedent("""\
                [backend]
                type = "paddle"
                api_url = "https://example.com/api"
                token = "tok"
            """)
        )
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF")

        from deepresearch_flow.ocr.config import BackendConfig, GeneralConfig, OcrConfig

        mock_load.return_value = OcrConfig(
            general=GeneralConfig(output_dir="default_out"),
            backend=BackendConfig(type="paddle", api_url="https://x", token="t"),
        )
        mock_run.return_value = {"processed": 1, "failed": 0, "skipped": 0}

        custom_out = str(tmp_path / "custom_output")
        runner = CliRunner()
        result = runner.invoke(
            recognize,
            ["ocr", str(pdf), "--config", str(config_path), "--output-dir", custom_out],
        )
        assert result.exit_code == 0
        # Verify run_ocr was called with the custom output dir.
        call_args = mock_run.call_args
        assert str(call_args[0][2]) == custom_out or str(call_args[1].get("output_dir", call_args[0][2])) == custom_out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_cli.py -v`
Expected: FAIL — `recognize` group has no `ocr` command

- [ ] **Step 3: Add the `ocr` subcommand to `recognize/cli.py`**

Add this after the `recognize` group definition (around line 447) in `python/deepresearch_flow/recognize/cli.py`:

```python
@recognize.command()
@click.argument("input_path", type=click.Path(exists=True))
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default="ocr.toml",
    help="Path to ocr.toml config file. Default: ocr.toml in current directory.",
)
@click.option(
    "--output-dir",
    type=click.Path(),
    default=None,
    help="Override output directory from config.",
)
def ocr(input_path: str, config_path: str, output_dir: str | None) -> None:
    """Run OCR on PDF/image files using a configured backend."""
    from pathlib import Path

    from deepresearch_flow.ocr.config import load_ocr_config
    from deepresearch_flow.ocr.factory import create_backend
    from deepresearch_flow.ocr.runner import run_ocr

    cfg_path = Path(config_path)
    try:
        cfg = load_ocr_config(cfg_path)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    backend = create_backend(cfg.backend)
    resolved_output = Path(output_dir) if output_dir else Path(cfg.general.output_dir)

    stats = run_ocr(backend, Path(input_path), resolved_output)

    click.echo(
        f"Done: {stats['processed']} processed, "
        f"{stats['failed']} failed, "
        f"{stats['skipped']} skipped."
    )
    if stats["failed"] > 0:
        raise click.ClickException(f"{stats['failed']} file(s) failed to process.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/test_cli.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run the full OCR test suite**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/ -v`
Expected: All tests PASS (base: 5, config: 10, factory: 2, paddle: 6, runner: 11, cli: 3 = ~37 total)

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/recognize/cli.py python/deepresearch_flow/ocr/tests/test_cli.py
git commit -m "feat(ocr): add 'recognize ocr' CLI subcommand"
```

---

## Task 7: Final Integration Verification

- [ ] **Step 1: Run the complete project test suite to check for regressions**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/ -v --tb=short 2>&1 | tail -30`
Expected: All existing tests + new OCR tests PASS. No regressions.

- [ ] **Step 2: Verify CLI help output**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run deepresearch-flow recognize ocr --help`
Expected: Shows help with `INPUT_PATH`, `--config`, and `--output-dir` options.

- [ ] **Step 3: Check test coverage for OCR module**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/ocr/tests/ --cov=deepresearch_flow.ocr --cov-report=term-missing`
Expected: Coverage >= 80%.

- [ ] **Step 4: Final commit with all files**

```bash
git add -A python/deepresearch_flow/ocr/ ocr.example.toml docs/
git commit -m "docs(ocr): add design spec, implementation plan, and example config"
```
