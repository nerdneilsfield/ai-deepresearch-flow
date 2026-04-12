from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64

from deepresearch_flow.paper.web.static_assets import StaticAssetConfig, build_static_assets, resolve_asset_urls


@dataclass
class _DummyIndex:
    md_path_by_hash: dict[str, Path]
    pdf_path_by_hash: dict[str, Path]
    translated_md_by_hash: dict[str, dict[str, Path]]


def test_build_static_assets_exports_files_and_rewrites_embedded_images(tmp_path: Path) -> None:
    payload = base64.b64encode(b"img-bytes").decode("ascii")
    md_path = tmp_path / "paper.md"
    md_path.write_text(f"![img](data:image/png;base64,{payload})", encoding="utf-8")
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")
    translated_path = tmp_path / "paper-zh.md"
    translated_path.write_text("# 中文", encoding="utf-8")

    index = _DummyIndex(
        md_path_by_hash={"hash-1": md_path},
        pdf_path_by_hash={"hash-1": pdf_path},
        translated_md_by_hash={"hash-1": {"zh": translated_path}},
    )
    export_dir = tmp_path / "static"

    config = build_static_assets(
        index,
        static_base_url="https://cdn.example.com/assets/",
        static_export_dir=export_dir,
    )

    assert config.enabled is True
    assert config.base_url == "https://cdn.example.com/assets"
    assert config.images_base_url == "https://cdn.example.com/assets/images"
    assert config.md_urls["hash-1"].startswith("https://cdn.example.com/assets/md/")
    assert config.pdf_urls["hash-1"].startswith("https://cdn.example.com/assets/pdf/")
    assert config.translated_md_urls["hash-1"]["zh"].startswith("https://cdn.example.com/assets/md_translate/zh/")

    exported_md = list((export_dir / "md").glob("*.md"))
    exported_pdf = list((export_dir / "pdf").glob("*.pdf"))
    exported_images = list((export_dir / "images").iterdir())
    exported_translated = list((export_dir / "md_translate" / "zh").glob("*.md"))

    assert len(exported_md) == 1
    assert "images/" in exported_md[0].read_text(encoding="utf-8")
    assert len(exported_pdf) == 1
    assert exported_pdf[0].read_bytes() == b"%PDF-1.7"
    assert len(exported_images) == 1
    assert exported_images[0].read_bytes() == b"img-bytes"
    assert len(exported_translated) == 1
    assert exported_translated[0].read_text(encoding="utf-8") == "# 中文"


def test_build_static_assets_disable_and_url_resolution(tmp_path: Path) -> None:
    md_path = tmp_path / "paper.md"
    md_path.write_text("source", encoding="utf-8")
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")
    translated_path = tmp_path / "paper-zh.md"
    translated_path.write_text("translated", encoding="utf-8")
    index = _DummyIndex(
        md_path_by_hash={"hash-1": md_path},
        pdf_path_by_hash={"hash-1": pdf_path},
        translated_md_by_hash={"hash-1": {"zh": translated_path}},
    )

    disabled = build_static_assets(index, static_base_url=None)
    assert disabled.enabled is False
    assert disabled.base_url is None

    empty_base = build_static_assets(index, static_base_url=None, allow_empty_base=True)
    assert empty_base.enabled is True
    assert empty_base.base_url == ""
    assert empty_base.images_base_url == "/images"

    empty_disabled = build_static_assets(index, static_base_url="")
    assert empty_disabled.enabled is False
    assert empty_disabled.base_url is None

    enabled_config = StaticAssetConfig(
        enabled=True,
        base_url="https://cdn.example.com",
        images_base_url="https://cdn.example.com/images",
        pdf_urls={"hash-1": "https://cdn.example.com/pdf/hash-1.pdf"},
        md_urls={"hash-1": "https://cdn.example.com/md/hash-1.md"},
        translated_md_urls={"hash-1": {"zh": "https://cdn.example.com/md_translate/zh/hash-1.md"}},
    )

    assert resolve_asset_urls(index, "hash-1", enabled_config, prefer_local=False) == {
        "pdf_url": "https://cdn.example.com/pdf/hash-1.pdf",
        "md_url": "https://cdn.example.com/md/hash-1.md",
        "md_translated_url": {"zh": "https://cdn.example.com/md_translate/zh/hash-1.md"},
        "images_base_url": "https://cdn.example.com/images",
    }
    assert resolve_asset_urls(index, "hash-1", enabled_config, prefer_local=True) == {
        "pdf_url": "/api/pdf/hash-1",
        "md_url": "/api/dev/markdown/hash-1",
        "md_translated_url": {"zh": "/api/dev/markdown/hash-1?lang=zh"},
        "images_base_url": "https://cdn.example.com/images",
    }
    assert resolve_asset_urls(index, "hash-1", None, prefer_local=False) == {
        "pdf_url": "/api/pdf/hash-1",
        "md_url": "/api/dev/markdown/hash-1",
        "md_translated_url": {"zh": "/api/dev/markdown/hash-1?lang=zh"},
        "images_base_url": None,
    }


def test_resolve_asset_urls_falls_back_when_static_asset_entry_is_missing() -> None:
    index = _DummyIndex(md_path_by_hash={}, pdf_path_by_hash={}, translated_md_by_hash={})
    asset_config = StaticAssetConfig(
        enabled=True,
        base_url="https://cdn.example.com",
        images_base_url="https://cdn.example.com/images",
        pdf_urls={"known": "https://cdn.example.com/pdf/known.pdf"},
        md_urls={"known": "https://cdn.example.com/md/known.md"},
        translated_md_urls={"known": {"zh": "https://cdn.example.com/md_translate/zh/known.md"}},
    )

    expected = {
        "pdf_url": None,
        "md_url": None,
        "md_translated_url": {},
        "images_base_url": "https://cdn.example.com/images",
    }
    assert resolve_asset_urls(index, "missing", asset_config, prefer_local=False) == expected
    assert resolve_asset_urls(index, "missing", asset_config, prefer_local=True) == expected
