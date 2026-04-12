from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

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


def _build_client(tmp_path: Path, *, pdf_only: bool = False) -> TestClient:
    md_path = tmp_path / "paper.md"
    md_path.write_text("# Source\n\nMore text", encoding="utf-8")
    zh_path = tmp_path / "paper.zh.md"
    zh_path.write_text("# 中文", encoding="utf-8")
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")

    paper = {
        "source_hash": "hash-1",
        "paper_title": "Graph Networks",
        "paper_authors": ["Alice"],
        "_authors": ["Alice"],
        "summary": "Attention paper",
        "_has_summary": True,
        "_venue": "ACL",
        "publication_venue": "ACL",
        "_year": "2024",
        "_month": "03",
        "_tags": ["vision"],
        "_template_tags": ["simple"],
        "_template_tags_lc": ["simple"],
        "_search_lc": "graph networks attention acl alice",
        "_title_lc": "graph networks",
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
    app = Starlette(
        routes=[
            Route("/", pages.index_page),
            Route("/stats", pages.stats_page),
            Route("/paper/{source_hash}", pages.paper_detail),
            Route("/robots.txt", pages.robots_txt),
        ]
    )
    app.state.index = index
    app.state.asset_config = _asset_config()
    app.state.static_mode = "dev"
    app.state.static_export_dir = None
    app.state.fallback_language = "en"
    app.state.pdfjs_cdn_base_url = "https://cdn.example.com/pdfjs"
    return TestClient(app)


def test_index_and_stats_pages_render_public_content(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    index = client.get("/")
    assert index.status_code == 200
    assert "Paper Database" in index.text
    assert "Search (Scholar-style)" in index.text
    assert "Open: Summary" in index.text

    stats = client.get("/stats")
    assert stats.status_code == 200
    assert "Stats" in stats.text
    assert "Charts are rendered with ECharts (CDN)." in stats.text

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert robots.text == "User-agent: *\nDisallow: /\n"


def test_paper_detail_renders_each_public_view(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    summary = client.get("/paper/hash-1")
    assert summary.status_code == 200
    assert "Graph Networks" in summary.text
    assert "Attention paper" in summary.text

    source = client.get("/paper/hash-1?view=source")
    assert source.status_code == 200
    assert "Rendered from source markdown:" in source.text
    assert "<h1>Source</h1>" in source.text
    assert "More text" in source.text

    translated = client.get("/paper/hash-1?view=translated&lang=zh")
    assert translated.status_code == 200
    assert "Language: zh" in translated.text
    assert "<h1>中文</h1>" in translated.text

    pdf = client.get("/paper/hash-1?view=pdf")
    assert pdf.status_code == 200
    assert "paper.pdf" in pdf.text
    assert "the-canvas" in pdf.text

    pdfjs = client.get("/paper/hash-1?view=pdfjs")
    assert pdfjs.status_code == 200
    assert "PDF.js Viewer" in pdfjs.text
    assert "/pdfjs/web/viewer.html?file=" in pdfjs.text

    split = client.get("/paper/hash-1?view=split&left=pdfjs&right=translated&lang=zh")
    assert split.status_code == 200
    assert "leftPane" in split.text
    assert "rightPane" in split.text
    assert "view=translated&amp;embed=1&amp;lang=zh" in split.text


def test_paper_detail_redirects_missing_and_handles_pdf_only_entries(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    missing = client.get("/paper/missing", follow_redirects=False)
    assert missing.status_code in {302, 307}
    assert missing.headers["location"] == "/"

    pdf_only_client = _build_client(tmp_path, pdf_only=True)
    pdf_only = pdf_only_client.get("/paper/hash-1?view=summary")
    assert pdf_only.status_code == 200
    assert "pdfjs-frame" in pdf_only.text
    assert "PDF.js Viewer" in pdf_only.text
    assert "paper.pdf" in pdf_only.text
