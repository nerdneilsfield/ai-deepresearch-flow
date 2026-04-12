from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from deepresearch_flow.paper.web import app as appmod


async def _ok(_: object) -> PlainTextResponse:
    return PlainTextResponse("ok")


def test_noindex_middleware_and_static_asset_files(tmp_path: Path) -> None:
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(appmod._NoIndexMiddleware)
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive, nosnippet, noai, noimageai"

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "app.js").write_text("console.log('ok')", encoding="utf-8")

    mounted = Starlette()
    mounted.mount(
        "/assets",
        appmod._StaticAssetFiles(directory=str(static_dir), cache_control="public, max-age=60"),
    )
    mounted_client = TestClient(mounted)
    asset_response = mounted_client.get("/assets/app.js")
    assert asset_response.status_code == 200
    assert asset_response.headers["Cache-Control"] == "public, max-age=60"


def test_static_mode_helpers() -> None:
    assert appmod._normalize_static_mode(None) == "auto"
    assert appmod._normalize_static_mode("development") == "dev"
    assert appmod._normalize_static_mode("production") == "prod"
    assert appmod._normalize_static_mode("weird") == "auto"

    assert appmod._resolve_static_mode("auto", "https://cdn.example.com") == "prod"
    assert appmod._resolve_static_mode("auto", None) == "dev"
    assert appmod._resolve_static_mode("dev", "https://cdn.example.com") == "dev"


def test_create_app_builds_state_and_routes(monkeypatch, tmp_path: Path) -> None:
    index = SimpleNamespace()
    build_static_calls: list[dict[str, object]] = []

    monkeypatch.setattr("deepresearch_flow.paper.web.app.load_and_merge_papers", lambda *args, **kwargs: ["paper"])
    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_index", lambda *args, **kwargs: index)
    monkeypatch.setattr("deepresearch_flow.paper.web.app.create_md_renderer", lambda: "renderer")

    def fake_build_static_assets(index_obj, **kwargs):  # noqa: ANN001
        build_static_calls.append(kwargs)
        base = kwargs.get("static_base_url")
        enabled = base is not None or kwargs.get("allow_empty_base") is True
        return SimpleNamespace(enabled=enabled, base_url=base, images_base_url=None)

    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_static_assets", fake_build_static_assets)

    pdfjs_dir = tmp_path / "pdfjs"
    pdfjs_dir.mkdir()
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    export_dir = tmp_path / "exported"
    for name in ("pdf", "images", "md", "md_translate"):
        (export_dir / name).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("deepresearch_flow.paper.web.app.PDFJS_STATIC_DIR", pdfjs_dir)
    monkeypatch.setattr("deepresearch_flow.paper.web.app.STATIC_DIR", static_dir)

    embed_db = tmp_path / "embed.db"
    embed_db.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr("deepresearch_flow.paper.vector_store.open_store", lambda path: f"store:{path.name}")

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

    assert app.state.index is index
    assert app.state.md == "renderer"
    assert app.state.fallback_language == "zh"
    assert app.state.static_mode == "prod"
    assert app.state.static_export_dir == export_dir
    assert app.state.pdfjs_cdn_base_url == "https://cdn.example.com/pdfjs"
    assert app.state.embed_db == "store:embed.db"
    assert app.state.search_access_token == "secret"
    assert app.state.paper_config == "paper-config"
    assert any(route.path == "/api/papers" for route in app.routes if hasattr(route, "path"))
    assert any(getattr(route, "name", "") == "pdfjs" for route in app.routes)
    assert any(getattr(route, "name", "") == "static" for route in app.routes)
    assert build_static_calls == [{"static_base_url": "https://cdn.example.com/assets/", "static_export_dir": export_dir}]


def test_create_app_falls_back_to_dev_export_and_local_pdfjs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("deepresearch_flow.paper.web.app.load_and_merge_papers", lambda *args, **kwargs: [])
    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_index", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr("deepresearch_flow.paper.web.app.create_md_renderer", lambda: "renderer")

    calls: list[dict[str, object]] = []

    def fake_build_static_assets(index_obj, **kwargs):  # noqa: ANN001
        calls.append(kwargs)
        base = kwargs.get("static_base_url")
        return SimpleNamespace(enabled=True, base_url=base, images_base_url=None)

    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_static_assets", fake_build_static_assets)
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
    assert calls == [{"static_base_url": "", "static_export_dir": export_dir, "allow_empty_base": True}]


def test_create_app_uses_disabled_assets_when_no_export_or_base(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("deepresearch_flow.paper.web.app.load_and_merge_papers", lambda *args, **kwargs: [])
    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_index", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr("deepresearch_flow.paper.web.app.create_md_renderer", lambda: "renderer")
    monkeypatch.setattr("deepresearch_flow.paper.web.app.PDFJS_STATIC_DIR", tmp_path / "missing-pdfjs")
    monkeypatch.setattr("deepresearch_flow.paper.web.app.STATIC_DIR", tmp_path / "missing-static")

    calls: list[dict[str, object]] = []

    def fake_build_static_assets(index_obj, **kwargs):  # noqa: ANN001
        calls.append(kwargs)
        return SimpleNamespace(enabled=False, base_url=None, images_base_url=None)

    monkeypatch.setattr("deepresearch_flow.paper.web.app.build_static_assets", fake_build_static_assets)

    app = appmod.create_app(db_paths=[tmp_path / "papers.db"])
    assert app.state.asset_config.enabled is False
    assert calls == [{"static_base_url": None}]
