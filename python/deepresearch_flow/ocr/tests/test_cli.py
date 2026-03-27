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
