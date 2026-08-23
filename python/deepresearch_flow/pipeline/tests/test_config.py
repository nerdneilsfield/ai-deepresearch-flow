from pathlib import Path

import pytest

from deepresearch_flow.pipeline.config import PipelineConfig, load_pipeline_config


def test_missing_pipeline_section_is_disabled_with_safe_defaults(tmp_path: Path) -> None:
    path = tmp_path / "service.toml"
    path.write_text("[paper]\nfoo = 'bar'\n", encoding="utf-8")

    config = load_pipeline_config(path)

    assert config.enabled is False
    assert config.pdfs_per_batch == 20
    assert config.max_pdf_bytes == 100 * 1024 * 1024
    assert config.max_batch_bytes == 500 * 1024 * 1024
    assert config.bibtex_max_bytes == 1024 * 1024
    assert config.max_concurrent_jobs == 2
    assert config.retention_days == 7


def test_pipeline_models_must_be_in_allowlists(tmp_path: Path) -> None:
    path = tmp_path / "service.toml"
    path.write_text(
        """
[pipeline]
enabled = false
[pipeline.models.ocr]
allowlist = ["ocr-a"]
default = "ocr-b"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ocr"):
        load_pipeline_config(path)


def test_public_snapshot_redacts_credentials(tmp_path: Path) -> None:
    path = tmp_path / "service.toml"
    path.write_text(
        """
[pipeline]
enabled = true
api_token = "secret-value"
[pipeline.models.ocr]
allowlist = ["ocr-a"]
default = "ocr-a"
[pipeline.models.extract]
allowlist = ["extract-a"]
default = "extract-a"
[pipeline.models.translate]
allowlist = ["translate-a"]
default = "translate-a"
""",
        encoding="utf-8",
    )

    snapshot = load_pipeline_config(path).public_snapshot()

    assert snapshot["enabled"] is True
    assert "secret-value" not in repr(snapshot)
    assert "api_token" not in snapshot


def test_nested_storage_and_selected_model_settings_are_supported(tmp_path: Path) -> None:
    path = tmp_path / "service.toml"
    path.write_text(
        """
[pipeline]
enabled = true
translation_language = "zh-Hant"
[pipeline.storage]
work_dir = "/srv/work"
queue_db = "/srv/queue.db"
snapshot_root = "/srv/snapshots"
static_root = "/srv/static"
webdav_url = "https://dav.example.test/library"
[pipeline.models.ocr]
allowlist = ["ocr-a", "ocr-b"]
selected = "ocr-b"
[pipeline.models.extract]
allowlist = ["extract-a"]
default = "extract-a"
[pipeline.models.translate]
allowlist = ["translate-a"]
default = "translate-a"
""",
        encoding="utf-8",
    )

    config = load_pipeline_config(path)

    assert config.work_dir == "/srv/work"
    assert config.queue_db == "/srv/queue.db"
    assert config.ocr.default == "ocr-b"
    assert config.translation_language == "zh-Hant"
    assert config.public_snapshot()["webdav_url"] == "https://dav.example.test/library"


def test_enabled_pipeline_requires_complete_nonempty_model_allowlists(tmp_path: Path) -> None:
    path = tmp_path / "service.toml"
    path.write_text("[pipeline]\nenabled = true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="allowlist"):
        load_pipeline_config(path)


def test_selected_model_overrides_default_and_false_is_strict_boolean(tmp_path: Path) -> None:
    path = tmp_path / "service.toml"
    path.write_text(
        """
[pipeline]
enabled = true
selected_models = { ocr = "ocr-b", extract = "extract-a", translate = "translate-a" }
[pipeline.models.ocr]
allowlist = ["ocr-a", "ocr-b"]
default = "ocr-a"
[pipeline.models.extract]
allowlist = ["extract-a"]
default = "extract-a"
[pipeline.models.translate]
allowlist = ["translate-a"]
default = "translate-a"
""",
        encoding="utf-8",
    )
    config = load_pipeline_config(path)
    assert config.ocr.default == "ocr-b"

    path.write_text("[pipeline]\nenabled = 'false'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="enabled"):
        load_pipeline_config(path)


def test_public_snapshot_and_fingerprint_strip_webdav_query_fragment(tmp_path: Path) -> None:
    path = tmp_path / "service.toml"
    path.write_text(
        '[pipeline]\nwebdav_url = "https://dav.example.test/lib?token=secret#private"\n',
        encoding="utf-8",
    )
    config = load_pipeline_config(path)
    assert config.public_snapshot()["webdav_url"] == "https://dav.example.test/lib"
    assert "secret" not in config.fingerprint()
