from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from deepresearch_flow.paper.web import app as appmod


def test_create_app_exposes_expected_routes_and_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("deepresearch_flow.paper.web.app.load_and_merge_papers", lambda *args, **kwargs: [])
    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_index", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr("deepresearch_flow.paper.web.app.create_md_renderer", lambda: "renderer")
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.app.build_static_assets",
        lambda index_obj, **kwargs: SimpleNamespace(enabled=True, base_url="https://cdn.example.com/assets", images_base_url="https://cdn.example.com/assets/images"),
    )

    pdfjs_dir = tmp_path / "pdfjs"
    pdfjs_dir.mkdir()
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    export_dir = tmp_path / "exported"
    for name in ("pdf", "images", "md", "md_translate"):
        (export_dir / name).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("deepresearch_flow.paper.web.app.PDFJS_STATIC_DIR", pdfjs_dir)
    monkeypatch.setattr("deepresearch_flow.paper.web.app.STATIC_DIR", static_dir)
    monkeypatch.setattr("deepresearch_flow.paper.vector_store.open_store", lambda path: f"store:{path.name}")

    embed_db = tmp_path / "embed.db"
    embed_db.write_text("placeholder", encoding="utf-8")

    app = appmod.create_app(
        db_paths=[tmp_path / "papers.db"],
        fallback_language="zh",
        md_roots=[tmp_path / "md"],
        md_translated_roots=[tmp_path / "md-translated"],
        pdf_roots=[tmp_path / "pdf"],
        static_base_url="https://cdn.example.com/assets/",
        static_mode="production",
        static_export_dir=export_dir,
        pdfjs_cdn_base_url="https://cdn.example.com/pdfjs/",
        embed_db=embed_db,
        search_access_token="secret",
        paper_config="paper-config",
    )

    assert app.state.index is not None
    assert app.state.md is not None
    assert app.state.fallback_language == "zh"
    assert app.state.static_mode == "prod"
    assert app.state.static_export_dir == export_dir
    assert app.state.pdfjs_cdn_base_url == "https://cdn.example.com/pdfjs"
    assert app.state.embed_db is not None
    assert app.state.search_access_token == "secret"
    assert app.state.paper_config == "paper-config"
    assert any(route.path == "/api/papers" for route in app.routes if hasattr(route, "path"))
    assert any(getattr(route, "name", "") == "pdfjs" for route in app.routes)
    assert any(getattr(route, "name", "") == "static" for route in app.routes)


def test_create_app_falls_back_to_dev_mode_for_local_pdfjs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("deepresearch_flow.paper.web.app.load_and_merge_papers", lambda *args, **kwargs: [])
    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_index", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr("deepresearch_flow.paper.web.app.create_md_renderer", lambda: "renderer")
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.app.build_static_assets",
        lambda index_obj, **kwargs: SimpleNamespace(enabled=True, base_url="", images_base_url="/images"),
    )
    monkeypatch.setattr("deepresearch_flow.paper.web.app.PDFJS_STATIC_DIR", tmp_path / "missing-pdfjs")
    monkeypatch.setattr("deepresearch_flow.paper.web.app.STATIC_DIR", tmp_path / "missing-static")

    export_dir = tmp_path / "exported"
    for name in ("pdf", "images", "md", "md_translate"):
        (export_dir / name).mkdir(parents=True, exist_ok=True)

    app = appmod.create_app(
        db_paths=[tmp_path / "papers.db"],
        pdf_roots=[tmp_path / "pdf"],
        static_mode="prod",
        static_export_dir=export_dir,
        pdfjs_cdn_base_url="local",
    )

    assert app.state.static_mode == "dev"
    assert app.state.pdfjs_cdn_base_url is None


def test_create_app_keeps_assets_disabled_when_no_export_or_base(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("deepresearch_flow.paper.web.app.load_and_merge_papers", lambda *args, **kwargs: [])
    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_index", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr("deepresearch_flow.paper.web.app.create_md_renderer", lambda: "renderer")
    monkeypatch.setattr("deepresearch_flow.paper.web.app.PDFJS_STATIC_DIR", tmp_path / "missing-pdfjs")
    monkeypatch.setattr("deepresearch_flow.paper.web.app.STATIC_DIR", tmp_path / "missing-static")
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.app.build_static_assets",
        lambda index_obj, **kwargs: SimpleNamespace(enabled=False, base_url=None, images_base_url=None),
    )

    app = appmod.create_app(db_paths=[tmp_path / "papers.db"])
    assert app.state.asset_config.enabled is False
