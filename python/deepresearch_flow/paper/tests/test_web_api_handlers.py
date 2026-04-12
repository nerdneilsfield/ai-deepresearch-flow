from __future__ import annotations

from dataclasses import dataclass
import asyncio
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from deepresearch_flow.paper.web.handlers.api import (
    _apply_query,
    _build_keyword_doc_ids,
    _embed_query,
    _ensure_under_roots,
    _safe_read_text,
    _paper_doc_id,
    _paper_text_for_rerank,
)
from deepresearch_flow.paper.web.query import Query, QueryTerm
from deepresearch_flow.paper.web.static_assets import StaticAssetConfig


@dataclass
class _DummyIndex:
    papers: list[dict]
    ordered_ids: list[int]
    md_path_by_hash: dict[str, Path]
    pdf_path_by_hash: dict[str, Path]
    translated_md_by_hash: dict[str, dict[str, Path]]
    template_tags: list[str]
    by_tag: dict[str, set[int]]
    by_author: dict[str, set[int]]
    by_month: dict[str, set[int]]
    by_year: dict[str, set[int]]
    stats: dict


def _build_index(tmp_path: Path) -> _DummyIndex:
    h1_md = tmp_path / "paper-1.md"
    h1_md.write_text("![img](embedded.png)\nsource", encoding="utf-8")
    h1_pdf = tmp_path / "paper-1.pdf"
    h1_pdf.write_bytes(b"%PDF-1.7 one")

    h2_md = tmp_path / "paper-2.md"
    h2_md.write_text("source 2", encoding="utf-8")
    h2_pdf = tmp_path / "paper-2.pdf"
    h2_pdf.write_bytes(b"%PDF-1.7 two")
    h2_zh = tmp_path / "paper-2-zh.md"
    h2_zh.write_text("translated markdown", encoding="utf-8")

    papers = [
        {
            "source_hash": "hash-1",
            "source_path": str(h1_md),
            "paper_title": "Graph Networks",
            "paper_authors": ["Alice"],
            "_authors": ["Alice"],
            "summary": "<b>Attention</b> paper",
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
        },
        {
            "source_hash": "hash-2",
            "source_path": str(h2_md),
            "paper_title": "Language Models",
            "paper_authors": ["Bob"],
            "_authors": ["Bob"],
            "summary": "",
            "_has_summary": False,
            "_venue": "NeurIPS",
            "publication_venue": "NeurIPS",
            "_year": "2023",
            "_month": "11",
            "_tags": ["nlp"],
            "_template_tags": ["deep_read"],
            "_template_tags_lc": ["deep_read"],
            "_search_lc": "language models neurips bob",
            "_title_lc": "language models",
        },
        {
            "source_hash": "hash-3",
            "source_path": str(tmp_path / "paper-3.md"),
            "paper_title": "Graph Theory Workshop",
            "paper_authors": ["Carol"],
            "_authors": ["Carol"],
            "summary": "notes",
            "_has_summary": True,
            "_venue": "Workshop",
            "publication_venue": "Workshop",
            "_year": "2022",
            "_month": "01",
            "_tags": ["vision"],
            "_template_tags": [],
            "_template_tags_lc": [],
            "_search_lc": "graph theory workshop carol",
            "_title_lc": "graph theory workshop",
            "_is_pdf_only": True,
        },
    ]

    return _DummyIndex(
        papers=papers,
        ordered_ids=[0, 1, 2],
        md_path_by_hash={"hash-1": h1_md, "hash-2": h2_md},
        pdf_path_by_hash={"hash-1": h1_pdf, "hash-2": h2_pdf},
        translated_md_by_hash={"hash-2": {"zh": h2_zh}},
        template_tags=["simple", "deep_read"],
        by_tag={"vision": {0, 2}, "nlp": {1}},
        by_author={"alice": {0}, "bob": {1}, "carol": {2}},
        by_month={"03": {0}, "11": {1}, "01": {2}},
        by_year={"2024": {0}, "2023": {1}, "2022": {2}},
        stats={"total": 3},
    )


def _asset_config() -> StaticAssetConfig:
    return StaticAssetConfig(
        enabled=True,
        base_url="",
        images_base_url="/images",
        pdf_urls={"hash-1": "/pdf/one.pdf", "hash-2": "/pdf/two.pdf"},
        md_urls={"hash-1": "/md/one.md", "hash-2": "/md/two.md"},
        translated_md_urls={"hash-2": {"zh": "/md_translate/zh/two.md"}},
    )


def _build_client(index: _DummyIndex, *, static_mode: str = "dev", export_dir: Path | None = None) -> TestClient:
    from deepresearch_flow.paper.web.handlers.api import api_markdown, api_papers, api_pdf, api_stats

    app = Starlette(
        routes=[
            Route("/api/papers", api_papers),
            Route("/api/stats", api_stats),
            Route("/api/pdf/{source_hash}", api_pdf),
            Route("/api/dev/markdown/{source_hash}", api_markdown),
        ]
    )
    app.state.index = index
    app.state.asset_config = _asset_config()
    app.state.static_mode = static_mode
    app.state.static_export_dir = export_dir
    app.state.pdf_roots = [export_dir] if export_dir else [Path(index.pdf_path_by_hash["hash-1"]).parent]
    return TestClient(app)


def test_api_handler_helpers_apply_query_and_doc_ids(tmp_path: Path, monkeypatch) -> None:
    index = _build_index(tmp_path)
    paper = index.papers[0]

    assert _paper_text_for_rerank(paper) == "Graph Networks\n<b>Attention</b> paper\nACL\nAlice"
    assert _ensure_under_roots(index.pdf_path_by_hash["hash-1"], [tmp_path]) is True
    assert _ensure_under_roots(index.pdf_path_by_hash["hash-1"], [tmp_path / "other"]) is False
    assert _paper_doc_id(paper) is not None

    doc_ids = _build_keyword_doc_ids(index, "graph", year=2024, venue="acl", limit=5)
    assert len(doc_ids) == 1
    assert _build_keyword_doc_ids(index, "graph", year=2023, venue="acl", limit=5) == []
    limited_ids = _build_keyword_doc_ids(index, "", year=None, venue=None, limit=1)
    assert len(limited_ids) == 1

    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.api.build_paper_key_candidates",
        lambda _: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert _paper_doc_id(paper) is None
    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api.build_paper_key_candidates", lambda _: [])
    assert _paper_doc_id(paper) is None

    query = Query(
        groups=[
            [
                QueryTerm(field="title", value="graph", negated=False),
                QueryTerm(field="venue", value="workshop", negated=True),
            ],
            [
                QueryTerm(field="tag", value="nlp", negated=False),
                QueryTerm(field=None, value="language", negated=False),
            ],
        ]
    )
    assert _apply_query(index, query) == {0, 1}

    range_query = Query(groups=[[QueryTerm(field="year", value="2023..2024", negated=False)]])
    assert _apply_query(index, range_query) == {0, 1}
    assert _apply_query(index, Query(groups=[[QueryTerm(field="tag", value="zzz", negated=False)]])) == set()
    assert _apply_query(index, Query(groups=[[QueryTerm(field="author", value="alice", negated=False)]])) == {0}
    assert _apply_query(index, Query(groups=[[QueryTerm(field="author", value="ali", negated=False)]])) == {0}
    assert _apply_query(index, Query(groups=[[QueryTerm(field="month", value="03", negated=False)]])) == {0}
    assert _apply_query(index, Query(groups=[[QueryTerm(field="month", value="3", negated=False)]])) == set()
    assert _apply_query(index, Query(groups=[[QueryTerm(field="year", value="2024", negated=False)]])) == {0}
    assert _apply_query(index, Query(groups=[[QueryTerm(field="year", value="202", negated=False)]])) == {0, 1, 2}
    assert _apply_query(index, Query(groups=[[QueryTerm(field="other", value="x", negated=False)]])) == set()

    latin1 = tmp_path / "latin1.txt"
    latin1.write_bytes("caf\xe9".encode("latin-1"))
    assert _safe_read_text(latin1) == "café"


def test_embed_query_requires_embedding_config() -> None:
    try:
        asyncio.run(_embed_query("graph", None, object()))
    except ValueError as exc:
        assert str(exc) == "Semantic search embedding config is unavailable"
    else:
        raise AssertionError("expected ValueError")


def test_api_stats_and_papers_routes(tmp_path: Path) -> None:
    index = _build_index(tmp_path)
    client = _build_client(index)

    stats_response = client.get("/api/stats")
    assert stats_response.status_code == 200
    assert stats_response.json() == {"total": 3}

    response = client.get("/api/papers?q=graph&fq=pdf:with&template=simple&sort_by=title&sort_dir=asc")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["total"] == 1
    assert data["stats"]["all"]["total"] == 2
    assert data["items"][0]["title"] == "Graph Networks"
    assert data["items"][0]["pdf_url"] == "/api/pdf/hash-1"
    assert data["items"][0]["md_url"] == "/api/dev/markdown/hash-1"

    second_page = client.get("/api/papers?page=2&page_size=1")
    assert second_page.status_code == 200
    assert second_page.json()["stats"] is None


def test_api_papers_filters_can_exclude_on_each_presence_dimension(tmp_path: Path) -> None:
    index = _build_index(tmp_path)
    index.papers.append(
        {
            "source_hash": "hash-4",
            "paper_title": "No Source",
            "_authors": ["Dana"],
            "_venue": "ICML",
            "_year": "2021",
            "_month": "07",
            "_tags": [],
            "_template_tags": [],
            "_template_tags_lc": [],
            "_search_lc": "no source",
            "_title_lc": "no source",
            "_has_summary": False,
        }
    )
    index.ordered_ids.append(3)

    client = _build_client(index)
    assert client.get("/api/papers?q=no&pdf=with").json()["total"] == 0
    assert client.get("/api/papers?q=no&source=with").json()["total"] == 0
    assert client.get("/api/papers?q=language&summary=with").json()["total"] == 0
    assert client.get("/api/papers?q=graph&translated=with&template=deep_read").json()["total"] == 0
    assert client.get("/api/papers?q=language&template=simple").json()["total"] == 0


def test_api_pdf_route_handles_missing_forbidden_and_success(tmp_path: Path) -> None:
    index = _build_index(tmp_path)
    client = _build_client(index, export_dir=tmp_path)

    missing = client.get("/api/pdf/missing")
    assert missing.status_code == 404

    client.app.state.pdf_roots = [tmp_path / "forbidden"]
    forbidden = client.get("/api/pdf/hash-1")
    assert forbidden.status_code == 403

    client.app.state.pdf_roots = [tmp_path]
    ok = client.get("/api/pdf/hash-1")
    assert ok.status_code == 200
    assert ok.content == b"%PDF-1.7 one"


def test_api_markdown_route_handles_modes_export_and_fallbacks(tmp_path: Path, monkeypatch) -> None:
    index = _build_index(tmp_path)
    export_dir = tmp_path / "static"
    (export_dir / "md").mkdir(parents=True)
    (export_dir / "md_translate" / "zh").mkdir(parents=True)
    (export_dir / "md" / "one.md").write_text("exported raw markdown", encoding="utf-8")
    (export_dir / "md_translate" / "zh" / "two.md").write_text("exported translated markdown", encoding="utf-8")

    client = _build_client(index, export_dir=export_dir)

    not_dev = _build_client(index, static_mode="prod")
    assert not_dev.get("/api/dev/markdown/hash-1").status_code == 404

    exported = client.get("/api/dev/markdown/hash-1")
    assert exported.status_code == 200
    assert exported.text == "exported raw markdown"

    exported_zh = client.get("/api/dev/markdown/hash-2?lang=zh")
    assert exported_zh.status_code == 200
    assert exported_zh.text == "exported translated markdown"

    client.app.state.static_export_dir = None
    client.app.state.asset_config = None
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.api.normalize_markdown_images",
        lambda text: f"normalized::{text}",
    )

    fallback = client.get("/api/dev/markdown/hash-1")
    assert fallback.status_code == 200
    assert fallback.text == "![img](embedded.png)\nsource"

    translated_fallback = client.get("/api/dev/markdown/hash-2?lang=zh")
    assert translated_fallback.status_code == 200
    assert translated_fallback.text == "normalized::translated markdown"

    missing = client.get("/api/dev/markdown/missing")
    assert missing.status_code == 404


def test_api_papers_semantic_item_payload_uses_asset_urls(tmp_path: Path, monkeypatch) -> None:
    from deepresearch_flow.paper.web.handlers.api import api_papers_semantic

    index = _build_index(tmp_path)
    app = Starlette(routes=[Route("/api/papers/semantic", api_papers_semantic)])
    app.state.search_access_token = None
    app.state.embed_db = object()
    app.state.index = index
    app.state.asset_config = _asset_config()
    app.state.static_mode = "dev"
    app.state.paper_config = SimpleNamespace(
        embedding=None,
        rerank=SimpleNamespace(enabled=False),
        search=SimpleNamespace(vector_top_k=20, keyword_top_k=10, hybrid=True),
    )
    client = TestClient(app)

    async def fake_embed_query(text, config, client_obj):  # noqa: ANN001, ARG001
        return [0.1, 0.2]

    async def fake_hybrid_search(**kwargs):  # noqa: ANN001
        return [
            SimpleNamespace(
                doc_id=_paper_doc_id(index.papers[0]),
                score=0.9,
                score_type="hybrid",
                matched_chunk="graph",
                matched_field="summary",
                matched_template="simple",
                matched_chunk_type="paragraph",
                matched_lang="",
            )
        ]

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query)
    monkeypatch.setattr("deepresearch_flow.paper.search.hybrid_search", fake_hybrid_search)

    response = client.get("/api/papers/semantic?q=graph&top_n=5")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["source_hash"] == "hash-1"
    assert payload["items"][0]["pdf_url"] == "/api/pdf/hash-1"


def test_api_papers_semantic_returns_503_and_400_and_builds_where(tmp_path: Path, monkeypatch) -> None:
    from deepresearch_flow.paper.web.handlers.api import api_papers_semantic

    unavailable = Starlette(routes=[Route("/api/papers/semantic", api_papers_semantic)])
    unavailable.state.search_access_token = None
    unavailable.state.embed_db = None
    unavailable_client = TestClient(unavailable)
    response = unavailable_client.get("/api/papers/semantic?q=graph")
    assert response.status_code == 503

    app = Starlette(routes=[Route("/api/papers/semantic", api_papers_semantic)])
    app.state.search_access_token = None
    app.state.embed_db = object()
    app.state.index = _build_index(tmp_path)
    app.state.asset_config = _asset_config()
    app.state.static_mode = "dev"
    app.state.paper_config = SimpleNamespace(
        embedding=None,
        rerank=SimpleNamespace(enabled=False),
        search=SimpleNamespace(vector_top_k=20, keyword_top_k=10, hybrid=True),
    )
    client = TestClient(app)

    missing_query = client.get("/api/papers/semantic")
    assert missing_query.status_code == 400

    seen: dict[str, object] = {}

    async def fake_embed_query(text, config, client_obj):  # noqa: ANN001, ARG001
        return [0.1, 0.2]

    async def fake_hybrid_search(**kwargs):  # noqa: ANN001
        seen["where"] = kwargs["where"]
        return []

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query)
    monkeypatch.setattr("deepresearch_flow.paper.search.hybrid_search", fake_hybrid_search)

    ok = client.get("/api/papers/semantic?q=graph&year=2024&venue=acl")
    assert ok.status_code == 200
    assert seen["where"] == 'year = 2024 AND venue = "acl"'
