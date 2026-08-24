from __future__ import annotations

from pathlib import Path
import pytest

from deepresearch_flow.paper.db import load_api_pipeline_config


def _pipeline_config(path: Path, *, enabled: bool) -> Path:
    path.write_text(
        "[pipeline]\n"
        f"enabled = {'true' if enabled else 'false'}\n"
        "[pipeline.models.ocr]\nallowlist=['ocr/test']\ndefault='ocr/test'\n"
        "[pipeline.models.extract]\nallowlist=['extract/test']\ndefault='extract/test'\n"
        "[pipeline.models.translate]\nallowlist=['translate/test']\ndefault='translate/test'\n",
        encoding="utf-8",
    )
    return path


def test_api_pipeline_config_loader_returns_enabled_public_configuration(tmp_path: Path) -> None:
    config = _pipeline_config(tmp_path / "config.toml", enabled=True)
    pipeline_config = load_api_pipeline_config(config, "admin-token")
    assert pipeline_config is not None
    assert pipeline_config.enabled is True
    assert pipeline_config.ocr.default == "ocr/test"


def test_api_serve_rejects_enabled_pipeline_without_admin_token(tmp_path: Path) -> None:
    config = _pipeline_config(tmp_path / "config.toml", enabled=True)
    with pytest.raises(ValueError, match="PAPER_DB_ADMIN_TOKEN"):
        load_api_pipeline_config(config, "")
