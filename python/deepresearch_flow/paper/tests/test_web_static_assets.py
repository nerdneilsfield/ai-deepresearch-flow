from __future__ import annotations

from dataclasses import dataclass
import base64
from pathlib import Path

import pytest

from deepresearch_flow.paper.web.static_assets import (
    StaticAssetConfig,
    _extension_from_mime,
    _normalize_base_url,
    _parse_data_url,
    _rewrite_markdown_images,
    _safe_read_text,
    _split_link_target,
    build_static_assets,
    resolve_asset_urls,
)


@dataclass
class _DummyIndex:
    md_path_by_hash: dict[str, Path]
    pdf_path_by_hash: dict[str, Path]
    translated_md_by_hash: dict[str, dict[str, Path]]


def test_static_asset_helper_parsing(monkeypatch) -> None:
    payload = base64.b64encode(b"abc").decode("ascii")

    assert _normalize_base_url("https://example.com/assets/") == "https://example.com/assets"
    assert _parse_data_url(f"data:image/png;base64,{payload}") == ("image/png", b"abc")
    assert _parse_data_url(f"data:text/plain;base64,{payload}") is None
    assert _parse_data_url("data:image/png,abc") is None
    assert _parse_data_url("not-a-data-url") is None

    def boom_decode(value: str) -> bytes:
        raise ValueError("bad base64")

    monkeypatch.setattr("deepresearch_flow.paper.web.static_assets.base64.b64decode", boom_decode)
    assert _parse_data_url("data:image/png;base64,abc") is None

    monkeypatch.setattr(
        "deepresearch_flow.paper.web.static_assets.mimetypes.guess_extension",
        lambda mime, strict=False: ".jpe",
    )
    assert _extension_from_mime("image/jpeg") == ".jpg"

    assert _split_link_target("<images/a.png> title") == ("images/a.png", " title", "<", ">")
    assert _split_link_target("images/a.png title") == ("images/a.png", " title", "", "")
    assert _split_link_target("") == ("", "", "", "")


def test_rewrite_markdown_images_exports_embedded_assets(tmp_path: Path) -> None:
    payload = base64.b64encode(b"png-bytes").decode("ascii")
    store_dir = tmp_path / "images"
    store_dir.mkdir()
    text = (
        f"![chart](data:image/png;base64,{payload})\n"
        f"<img alt='chart' src='data:image/png;base64,{payload}'>\n"
        "![keep](plain.png)"
    )

    from deepresearch_flow.paper.web.static_assets import _ImageStore

    rewritten = _rewrite_markdown_images(text, _ImageStore(store_dir))

    assert "images/" in rewritten
    assert "plain.png" in rewritten
    written = list(store_dir.iterdir())
    assert len(written) == 1
    assert written[0].suffix == ".png"
    assert written[0].read_bytes() == b"png-bytes"

    from deepresearch_flow.paper.web.static_assets import _ImageStore

    store = _ImageStore(store_dir)
    assert store.add_image("image/png", b"png-bytes").startswith("images/")
    assert store._written
    assert _rewrite_markdown_images("![x](plain.png)", store) == "![x](plain.png)"
    assert _rewrite_markdown_images("<img alt='x'>", store) == "<img alt='x'>"
    assert _rewrite_markdown_images("<img src='plain.png'>", store) == "<img src='plain.png'>"
    assert _rewrite_markdown_images("<img src='data:text/plain;base64,YQ=='>", store) == "<img src='data:text/plain;base64,YQ=='>"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.static_assets.mimetypes.guess_extension",
        lambda mime, strict=False: None,
    )
    try:
        no_ext_store = _ImageStore(store_dir)
        assert no_ext_store.add_image("image/unknown", b"data") is None
    finally:
        monkeypatch.undo()

    class _NoWriteStore:
        def add_image(self, mime: str, data: bytes):  # noqa: ANN001
            return None

    raw_md = f"![chart](data:image/png;base64,{payload})"
    assert _rewrite_markdown_images(raw_md, _NoWriteStore()) == raw_md
    raw_img = f"<img src=data:image/png;base64,{payload}>"
    assert _rewrite_markdown_images(raw_img, _NoWriteStore()) == raw_img
    assert "images/" in _rewrite_markdown_images(f"<img src=data:image/png;base64,{payload}>", _ImageStore(store_dir))


def test_safe_read_text_falls_back_to_latin1(tmp_path: Path) -> None:
    path = tmp_path / "latin1.md"
    path.write_bytes("caf\xe9".encode("latin-1"))
    assert _safe_read_text(path) == "café"


def test_build_static_assets_writes_export_tree(tmp_path: Path) -> None:
    payload = base64.b64encode(b"img-bytes").decode("ascii")
    md_path = tmp_path / "paper.md"
    md_path.write_text(f"![img](data:image/png;base64,{payload})", encoding="utf-8")
    translated_path = tmp_path / "paper-zh.md"
    translated_path.write_text("# 中文", encoding="utf-8")
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")

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
    assert config.translated_md_urls["hash-1"]["zh"].startswith(
        "https://cdn.example.com/assets/md_translate/zh/"
    )

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


def test_build_static_assets_can_be_disabled_and_resolve_urls(tmp_path: Path) -> None:
    (tmp_path / "paper.md").write_text("source", encoding="utf-8")
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.7")
    (tmp_path / "paper-zh.md").write_text("translated", encoding="utf-8")
    index = _DummyIndex(
        md_path_by_hash={"hash-1": tmp_path / "paper.md"},
        pdf_path_by_hash={"hash-1": tmp_path / "paper.pdf"},
        translated_md_by_hash={"hash-1": {"zh": tmp_path / "paper-zh.md"}},
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
