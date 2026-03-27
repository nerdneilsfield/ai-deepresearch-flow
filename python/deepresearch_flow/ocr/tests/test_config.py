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
