"""Tests for OCR config loading."""

from __future__ import annotations

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
            model = "PaddleOCR-VL-1.6"
            poll_interval_seconds = 2.5
            job_timeout_seconds = 600

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
        assert cfg.backend.model == "PaddleOCR-VL-1.6"
        assert cfg.backend.poll_interval_seconds == 2.5
        assert cfg.backend.job_timeout_seconds == 600.0

    def test_env_prefix_resolution(self, env_toml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_OCR_TOKEN", "resolved-secret")
        cfg = load_ocr_config(env_toml)
        assert cfg.backend.token == "resolved-secret"

    def test_env_prefix_missing_raises(
        self, env_toml: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        assert cfg.backend.model == "PaddleOCR-VL-1.6"
        assert cfg.backend.poll_interval_seconds == 5.0
        assert cfg.backend.job_timeout_seconds == 1800.0

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

    def test_unsupported_paddle_model_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "ocr.toml"
        p.write_text(
            textwrap.dedent("""\
                [backend]
                type = "paddle"
                api_url = "https://example.com/api/v2/ocr/jobs"
                token = "tok"
                model = "PP-OCRv6"
            """)
        )

        with pytest.raises(ValueError, match="PaddleOCR-VL-1.6"):
            load_ocr_config(p)

    @pytest.mark.parametrize("field_name", ["poll_interval_seconds", "job_timeout_seconds"])
    def test_non_positive_job_timing_raises(self, tmp_path: Path, field_name: str) -> None:
        p = tmp_path / "ocr.toml"
        p.write_text(
            textwrap.dedent(f"""\
                [backend]
                type = "paddle"
                api_url = "https://example.com/api/v2/ocr/jobs"
                token = "tok"
                {field_name} = 0
            """)
        )

        with pytest.raises(ValueError, match=field_name):
            load_ocr_config(p)
