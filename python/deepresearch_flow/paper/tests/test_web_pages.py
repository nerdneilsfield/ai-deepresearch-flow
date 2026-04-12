from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.requests import Request

from deepresearch_flow.paper.web.handlers import pages
from deepresearch_flow.paper.web.static_assets import StaticAssetConfig


@dataclass
class _DummyIndex:
    papers: list[dict]
    id_by_hash: dict[str, int]
    md_path_by_hash: dict[str, Path]
    pdf_path_by_hash: dict[str, Path]
    translated_md_by_hash: dict[str, dict[str, Path]]
    template_tags: list[str]


def _asset_config() -> StaticAssetConfig:
    return StaticAssetConfig(
        enabled=True,
        base_url="",
        images_base_url="/images",
        pdf_urls={"hash-1": "/pdf/hash-1.pdf"},
        md_urls={"hash-1": "/md/hash-1.md"},
        translated_md_urls={"hash-1": {"zh": "/md_translate/zh/hash-1.md"}},
    )


def _request(app: Starlette, path: str, source_hash: str = "hash-1", query_string: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": query_string.encode("utf-8"),
        "app": app,
        "path_params": {"source_hash": source_hash},
    }
    return Request(scope)


def _build_app(tmp_path: Path, *, pdf_only: bool = False) -> Starlette:
    md_path = tmp_path / "paper.md"
    md_path.write_text("# Source", encoding="utf-8")
    zh_path = tmp_path / "paper.zh.md"
    zh_path.write_text("# 中文", encoding="utf-8")
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")

    paper = {
        "source_hash": "hash-1",
        "paper_title": "Graph Networks",
        "_is_pdf_only": pdf_only,
    }
    index = _DummyIndex(
        papers=[paper],
        id_by_hash={"hash-1": 0},
        md_path_by_hash={} if pdf_only else {"hash-1": md_path},
        pdf_path_by_hash={"hash-1": pdf_path},
        translated_md_by_hash={} if pdf_only else {"hash-1": {"zh": zh_path}},
        template_tags=["simple", "deep_read"],
    )
    app = Starlette()
    app.state.index = index
    app.state.asset_config = _asset_config()
    app.state.static_mode = "dev"
    app.state.static_export_dir = None
    app.state.fallback_language = "en"
    app.state.pdfjs_cdn_base_url = "https://cdn.example.com/pdfjs"
    return app


def test_page_helpers_safe_read_and_load_markdown(tmp_path: Path, monkeypatch) -> None:
    latin1 = tmp_path / "latin1.md"
    latin1.write_bytes("caf\xe9".encode("latin-1"))
    assert pages._safe_read_text(latin1) == "café"

    app = _build_app(tmp_path)
    index = app.state.index

    export_dir = tmp_path / "static"
    (export_dir / "md").mkdir(parents=True)
    (export_dir / "md_translate" / "zh").mkdir(parents=True)
    (export_dir / "md" / "hash-1.md").write_text("export source", encoding="utf-8")
    (export_dir / "md_translate" / "zh" / "hash-1.md").write_text("export zh", encoding="utf-8")

    exported = pages._load_markdown_for_view(index, _asset_config(), export_dir, "hash-1")
    assert exported == "export source"

    exported_zh = pages._load_markdown_for_view(index, _asset_config(), export_dir, "hash-1", lang="zh")
    assert exported_zh == "export zh"

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.normalize_markdown_images", lambda text: f"norm::{text}")
    raw = pages._load_markdown_for_view(index, None, None, "hash-1")
    assert raw == "# Source"
    translated = pages._load_markdown_for_view(index, None, None, "hash-1", lang="zh")
    assert translated == "norm::# 中文"
    assert pages._load_markdown_for_view(index, None, None, "missing") is None


def test_basic_pages_render_index_stats_and_robots(tmp_path: Path, monkeypatch) -> None:
    app = _build_app(tmp_path)
    seen: dict[str, object] = {}

    def fake_render_template(template_name: str, **context):
        seen["template_name"] = template_name
        seen.update(context)
        return f"rendered:{template_name}"

    monkeypatch.setattr("deepresearch_flow.paper.web.templates.render_template", fake_render_template)

    robots = asyncio.run(pages.robots_txt(_request(app, "/robots.txt")))
    assert robots.body.decode("utf-8") == "User-agent: *\nDisallow: /\n"

    index_response = asyncio.run(pages.index_page(_request(app, "/")))
    assert index_response.body.decode("utf-8") == "rendered:index.html"
    assert seen["title"] == "Paper DB"
    assert "&#10;" in str(seen["filter_help"])

    stats_response = asyncio.run(pages.stats_page(_request(app, "/stats")))
    assert stats_response.body.decode("utf-8") == "rendered:stats.html"


def test_paper_detail_summary_and_missing_redirect(tmp_path: Path, monkeypatch) -> None:
    app = _build_app(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.resolve_asset_urls", lambda *args, **kwargs: {
        "pdf_url": "/api/pdf/hash-1",
        "md_url": "/api/dev/markdown/hash-1",
        "md_translated_url": {"zh": "/api/dev/markdown/hash-1?lang=zh"},
        "images_base_url": "/images",
    })
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.select_template_tag", lambda paper, tag: ("simple", ["simple", "deep_read"]))
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_paper_markdown",
        lambda paper, fallback_language, template_tag=None: ("# Summary", "simple", "<div>warn</div>"),
    )
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.create_md_renderer", lambda: "renderer")
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_markdown_with_math_placeholders",
        lambda renderer, markdown: f"<p>{markdown}</p>",
    )
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_template",
        lambda template_name, **context: captured.update({"template_name": template_name, **context}) or "detail-html",
    )

    response = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1")))
    assert response.body.decode("utf-8") == "detail-html"
    assert captured["template_name"] == "detail.html"
    assert captured["current_view"] == "summary"
    assert captured["show_outline"] is True
    assert captured["body_html"] == "<p># Summary</p>"
    assert "templateSelect" in str(captured["template_controls"])
    assert captured["pdf_url"] == "/api/pdf/hash-1"

    app.state.index.id_by_hash = {}
    redirect = asyncio.run(pages.paper_detail(_request(app, "/paper/missing", source_hash="missing")))
    assert redirect.status_code in {307, 302}
    assert redirect.headers["location"] == "/"


def test_paper_detail_source_translated_pdfjs_and_split(tmp_path: Path, monkeypatch) -> None:
    app = _build_app(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.resolve_asset_urls", lambda *args, **kwargs: {
        "pdf_url": "/api/pdf/hash-1",
        "md_url": "/api/dev/markdown/hash-1",
        "md_translated_url": {"zh": "/api/dev/markdown/hash-1?lang=zh"},
        "images_base_url": "/images",
    })
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.create_md_renderer", lambda: "renderer")
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_markdown_with_math_placeholders",
        lambda renderer, markdown: f"<p>{markdown}</p>",
    )
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.build_pdfjs_viewer_url",
        lambda pdf_url, cdn_base_url=None: f"viewer:{pdf_url}:{cdn_base_url}",
    )
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_template",
        lambda template_name, **context: captured.update({"template_name": template_name, **context}) or "detail-html",
    )
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages._load_markdown_for_view", lambda *args, **kwargs: "# Rendered")

    source = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=source")))
    assert source.body.decode("utf-8") == "detail-html"
    assert captured["current_view"] == "source"
    assert captured["body_html"] == "<p># Rendered</p>"
    assert captured["source_markdown_url"] == "/api/dev/markdown/hash-1"

    translated = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=translated&lang=zh")))
    assert translated.body.decode("utf-8") == "detail-html"
    assert captured["current_view"] == "translated"
    assert captured["translated_markdown_url"] == "/api/dev/markdown/hash-1?lang=zh"
    assert captured["selected_lang"] == "zh"

    pdfjs = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=pdfjs")))
    assert pdfjs.body.decode("utf-8") == "detail-html"
    assert captured["current_view"] == "pdfjs"
    assert captured["pdfjs_url"] == "viewer:/api/pdf/hash-1:https://cdn.example.com/pdfjs"

    split = asyncio.run(
        pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=split&left=pdfjs&right=translated&lang=zh"))
    )
    assert split.body.decode("utf-8") == "detail-html"
    assert captured["current_view"] == "split"
    assert captured["left_src"] == "viewer:/api/pdf/hash-1:https://cdn.example.com/pdfjs"
    assert captured["right_src"] == "/paper/hash-1?view=translated&embed=1&lang=zh"

    split_template = asyncio.run(
        pages.paper_detail(
            _request(app, "/paper/hash-1", query_string="view=split&left=summary&right=pdf&template=deep_read&extra=1")
        )
    )
    assert split_template.body.decode("utf-8") == "detail-html"
    assert captured["left_src"] == "/paper/hash-1?view=summary&embed=1&template=deep_read"


def test_paper_detail_warning_and_embed_branches(tmp_path: Path, monkeypatch) -> None:
    app = _build_app(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_template",
        lambda template_name, **context: captured.update({"template_name": template_name, **context}) or "detail-html",
    )
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.resolve_asset_urls", lambda *args, **kwargs: {
        "pdf_url": None,
        "md_url": None,
        "md_translated_url": {},
        "images_base_url": None,
    })
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.select_template_tag", lambda paper, tag: ("simple", []))

    source = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=source")))
    assert source.body.decode("utf-8") == "detail-html"
    assert "Source markdown not found" in str(captured["body_html"])

    translated_missing = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=translated")))
    assert translated_missing.body.decode("utf-8") == "detail-html"
    assert "Translated markdown not found for the selected language" in str(captured["body_html"])

    app.state.index.translated_md_by_hash = {}
    translated_absent = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=translated")))
    assert translated_absent.body.decode("utf-8") == "detail-html"
    assert "No translated markdown found" in str(captured["body_html"])

    app.state.pdfjs_cdn_base_url = None
    pdf = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=pdf&embed=1")))
    assert pdf.body.decode("utf-8") == "detail-html"
    assert "PDF not found" in str(captured["body_html"])
    assert captured["pdfjs_script_url"] == "/pdfjs/build/pdf.js"
    assert "embed-view" in str(captured["body_class"])

    pdfjs = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=pdfjs")))
    assert pdfjs.body.decode("utf-8") == "detail-html"
    assert "PDF not found" in str(captured["body_html"])


def test_paper_detail_uses_first_translation_when_no_zh(tmp_path: Path, monkeypatch) -> None:
    app = _build_app(tmp_path)
    app.state.index.translated_md_by_hash = {"hash-1": {"fr": tmp_path / "paper.fr.md"}}
    captured: dict[str, object] = {}

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.resolve_asset_urls", lambda *args, **kwargs: {
        "pdf_url": "/api/pdf/hash-1",
        "md_url": "/api/dev/markdown/hash-1",
        "md_translated_url": {"fr": "/api/dev/markdown/hash-1?lang=fr"},
        "images_base_url": None,
    })
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages._load_markdown_for_view", lambda *args, **kwargs: "# FR")
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.create_md_renderer", lambda: "renderer")
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_markdown_with_math_placeholders",
        lambda renderer, markdown: f"<p>{markdown}</p>",
    )
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_template",
        lambda template_name, **context: captured.update({"template_name": template_name, **context}) or "detail-html",
    )

    response = asyncio.run(pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=translated")))
    assert response.body.decode("utf-8") == "detail-html"
    assert captured["selected_lang"] == "fr"


def test_paper_detail_pdf_only_modes(tmp_path: Path, monkeypatch) -> None:
    app = _build_app(tmp_path, pdf_only=True)
    captured: dict[str, object] = {}

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.pages.resolve_asset_urls", lambda *args, **kwargs: {
        "pdf_url": "/api/pdf/hash-1",
        "md_url": None,
        "md_translated_url": {},
        "images_base_url": None,
    })
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.build_pdfjs_viewer_url",
        lambda pdf_url, cdn_base_url=None: f"viewer:{pdf_url}",
    )
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.pages.render_template",
        lambda template_name, **context: captured.update({"template_name": template_name, **context}) or "detail-html",
    )

    response = asyncio.run(
        pages.paper_detail(_request(app, "/paper/hash-1", query_string="view=split&left=pdf&right=pdfjs"))
    )
    assert response.body.decode("utf-8") == "detail-html"
    assert captured["is_pdf_only"] is True
    assert [label for label, _ in captured["tabs"]] == ["PDF", "PDF Viewer", "Split"]
    assert captured["split_options"] == [("pdf", "PDF"), ("pdfjs", "PDF Viewer")]
