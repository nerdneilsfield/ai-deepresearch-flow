from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from deepresearch_flow.paper.config import (
    BaseConfig,
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
    EmbeddingConfig,
    EmbeddingModelConfig,
    EmbeddingProviderConfig,
    KeyConfig,
    PaperConfig,
    RerankConfig,
    RerankModelConfig,
    RerankProviderConfig,
    SearchConfig,
)
from deepresearch_flow.paper.vector_store import (
    ChunkRow,
    INDEX_VERSION,
    open_store,
    save_index_meta,
    write_chunks,
)
from deepresearch_flow.paper.web.handlers.api import (
    api_markdown,
    api_papers,
    api_papers_semantic,
    api_pdf,
    api_stats,
)
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
    id_by_hash: dict[str, int]


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
        id_by_hash={"hash-1": 0, "hash-2": 1, "hash-3": 2},
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


def _build_papers_client(
    tmp_path: Path, *, static_mode: str = "dev", export_dir: Path | None = None
) -> TestClient:
    app = Starlette(
        routes=[
            Route("/api/papers", api_papers),
            Route("/api/stats", api_stats),
            Route("/api/pdf/{source_hash}", api_pdf),
            Route("/api/dev/markdown/{source_hash}", api_markdown),
        ]
    )
    app.state.index = _build_index(tmp_path)
    app.state.asset_config = _asset_config()
    app.state.static_mode = static_mode
    app.state.static_export_dir = export_dir
    app.state.pdf_roots = (
        [export_dir] if export_dir else [Path(app.state.index.pdf_path_by_hash["hash-1"]).parent]
    )
    return TestClient(app)


def _semantic_config() -> PaperConfig:
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[],
        main_model=[],
        embedding=EmbeddingConfig(
            default_model="bge-m3",
            default_provider="ollama",
            dimensions=1024,
            normalized=True,
            batch_size=2,
            chunk_max_tokens=512,
            chunk_overlap_tokens=64,
            providers=[
                EmbeddingProviderConfig(
                    name="ollama",
                    type="openai_compatible",
                    base=[
                        BaseConfig(
                            url="http://localhost:11434/v1",
                            weight=1,
                            key=[KeyConfig(value="embedding-api-key", weight=1)],
                        )
                    ],
                    models=[
                        EmbeddingModelConfig(model_name="bge-m3", dimensions=1024, max_context=8192)
                    ],
                )
            ],
        ),
        rerank=RerankConfig(
            enabled=True,
            default_model="bge-reranker-v2-m3",
            default_provider="siliconflow",
            top_n=10,
            providers=[
                RerankProviderConfig(
                    name="siliconflow",
                    type="openai_compatible",
                    base=[
                        BaseConfig(
                            url="https://api.siliconflow.cn/v1",
                            weight=1,
                            key=[KeyConfig(value="rerank-api-key", weight=1)],
                        )
                    ],
                    models=[
                        RerankModelConfig(
                            model_name="bge-reranker-v2-m3",
                            max_context=2048,
                            max_chunks_per_doc=64,
                            instruction="Rank by relevance",
                        )
                    ],
                )
            ],
        ),
        search=SearchConfig(
            vector_dir="paper_vectors", vector_top_k=50, keyword_top_k=30, hybrid=True
        ),
    )


def _create_semantic_db(tmp_path: Path, *, dimensions: int = 1024) -> Path:
    embed_dir = tmp_path / "embed_vectors"
    embed_dir.mkdir()
    db = open_store(embed_dir)
    rows = [
        ChunkRow(
            id="hash-1__shared_title_0",
            doc_id="hash-1",
            source_path="paper-1.md",
            template_tag="",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="Attention Is All You Need",
            content_hash="abc",
            vector=[0.1] * dimensions,
            title="Attention Is All You Need",
            year=2017,
            authors="Vaswani",
            venue="NeurIPS",
            tags="transformer",
        ),
    ]
    write_chunks(db, rows, dimensions=dimensions)
    save_index_meta(
        embed_dir,
        {
            "model": "bge-m3",
            "dimensions": 1024,
            "normalized": True,
            "provider": "test",
            "index_version": INDEX_VERSION,
        },
    )
    return embed_dir


def test_api_routes_return_expected_payloads(tmp_path: Path) -> None:
    client = _build_papers_client(tmp_path)

    stats_response = client.get("/api/stats")
    assert stats_response.status_code == 200
    assert stats_response.json() == {"total": 3}

    paged = client.get("/api/papers?page=1&page_size=2")
    assert paged.status_code == 200
    paged_data = paged.json()
    assert paged_data["page"] == 1
    assert paged_data["page_size"] == 2
    assert paged_data["has_more"] is True
    assert [item["title"] for item in paged_data["items"]] == ["Graph Networks", "Language Models"]

    desc_sorted = client.get("/api/papers?page=1&page_size=1&sort_by=title&sort_dir=desc")
    assert desc_sorted.status_code == 200
    assert desc_sorted.json()["items"][0]["title"] == "Language Models"

    response = client.get(
        "/api/papers?q=graph&fq=pdf:with&template=simple&sort_by=title&sort_dir=asc"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["total"] == 1
    assert data["stats"]["all"]["total"] == 2
    assert data["items"][0]["title"] == "Graph Networks"
    assert data["items"][0]["pdf_url"] == "/api/pdf/hash-1"
    assert data["items"][0]["md_url"] == "/api/dev/markdown/hash-1"
    assert data["stats"]["filtered"]["total"] == 1

    second_page = client.get("/api/papers?page=2&page_size=1")
    assert second_page.status_code == 200
    assert second_page.json()["stats"] is None

    translated = client.get("/api/papers?template=deep_read&translated=with")
    assert translated.status_code == 200
    translated_data = translated.json()
    assert translated_data["total"] == 1
    assert translated_data["items"][0]["title"] == "Language Models"
    assert translated_data["items"][0]["has_translation"] is True
    assert translated_data["items"][0]["md_translated_url"] == {
        "zh": "/api/dev/markdown/hash-2?lang=zh"
    }

    assert client.get("/api/papers?q=no&pdf=with").json()["total"] == 0
    assert client.get("/api/papers?q=no&source=with").json()["total"] == 0
    assert client.get("/api/papers?q=language&summary=with").json()["total"] == 0
    assert client.get("/api/papers?q=graph&translated=with&template=deep_read").json()["total"] == 0
    assert client.get("/api/papers?q=language&template=simple").json()["total"] == 0

    client.app.state.index.papers[1].update(
        {
            "paper_title": "Graph",
            "_title_lc": "graph",
            "_venue": "Networks",
            "publication_venue": "Networks",
            "_search_lc": "graph networks bob",
        }
    )
    phrase = client.get("/api/papers", params={"q": '"graph networks"'})
    assert phrase.status_code == 200
    assert [item["title"] for item in phrase.json()["items"]] == ["Graph Networks"]

    words = client.get("/api/papers", params={"q": "graph networks"})
    assert words.status_code == 200
    assert [item["title"] for item in words.json()["items"]] == ["Graph Networks"]


def test_api_pdf_and_markdown_routes_handle_export_and_fallbacks(tmp_path: Path) -> None:
    export_dir = tmp_path / "static"
    (export_dir / "md").mkdir(parents=True)
    (export_dir / "md_translate" / "zh").mkdir(parents=True)
    (export_dir / "md" / "one.md").write_text("exported raw markdown", encoding="utf-8")
    (export_dir / "md_translate" / "zh" / "two.md").write_text(
        "exported translated markdown", encoding="utf-8"
    )

    client = _build_papers_client(tmp_path, export_dir=export_dir)

    missing = client.get("/api/pdf/missing")
    assert missing.status_code == 404

    client.app.state.pdf_roots = [tmp_path / "forbidden"]
    forbidden = client.get("/api/pdf/hash-1")
    assert forbidden.status_code == 403

    client.app.state.pdf_roots = [tmp_path]
    ok = client.get("/api/pdf/hash-1")
    assert ok.status_code == 200
    assert ok.content == b"%PDF-1.7 one"

    not_dev = _build_papers_client(tmp_path, static_mode="prod")
    assert not_dev.get("/api/dev/markdown/hash-1").status_code == 404

    exported = client.get("/api/dev/markdown/hash-1")
    assert exported.status_code == 200
    assert exported.text == "exported raw markdown"

    exported_zh = client.get("/api/dev/markdown/hash-2?lang=zh")
    assert exported_zh.status_code == 200
    assert exported_zh.text == "exported translated markdown"

    client.app.state.static_export_dir = None
    client.app.state.asset_config = None

    fallback = client.get("/api/dev/markdown/hash-1")
    assert fallback.status_code == 200
    assert fallback.text == "![img](embedded.png)\nsource"

    translated_fallback = client.get("/api/dev/markdown/hash-2?lang=zh")
    assert translated_fallback.status_code == 200
    assert translated_fallback.text == "translated markdown"

    missing_md = client.get("/api/dev/markdown/missing")
    assert missing_md.status_code == 404


def test_api_semantic_routes_enforce_contracts_and_return_results(
    tmp_path: Path, monkeypatch
) -> None:
    embed_dir = _create_semantic_db(tmp_path, dimensions=1024)
    app = Starlette(routes=[Route("/api/papers/semantic", api_papers_semantic)])
    app.state.embed_db = open_store(embed_dir)
    app.state.search_access_token = "secret-token"
    app.state.paper_config = _semantic_config()
    app.state.index = _build_index(tmp_path)
    app.state.asset_config = _asset_config()
    app.state.static_mode = "dev"
    client = TestClient(app)

    response = client.get("/api/papers/semantic?q=attention&top_n=5")
    assert response.status_code == 403

    response = client.get(
        "/api/papers/semantic?q=attention&top_n=5",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 403

    async def boom_embed_query(text, config, client_obj):  # noqa: ANN001, ARG001
        raise AssertionError("embedding should not run for probe requests")

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", boom_embed_query)
    probe = client.get(
        "/api/papers/semantic?probe=1", headers={"Authorization": "Bearer secret-token"}
    )
    assert probe.status_code == 200
    assert probe.json() == {"ok": True}

    app.state.search_access_token = None
    client = TestClient(app)
    assert client.get("/api/papers/semantic").status_code == 400

    async def fake_call_embedding(
        base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None
    ):  # noqa: ANN001, ARG001
        return type("EmbeddingResult", (), {"vectors": [[0.1] * 1024]})()

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_call_embedding)

    async def fake_hybrid_search(**kwargs):  # noqa: ANN001
        return [
            SimpleNamespace(
                doc_id="hash-1",
                score=0.9,
                score_type="hybrid",
                matched_chunk="graph",
                matched_field="summary",
                matched_template="simple",
                matched_chunk_type="paragraph",
                matched_lang="",
            )
        ]

    monkeypatch.setattr("deepresearch_flow.paper.search.hybrid_search", fake_hybrid_search)

    ok = client.get("/api/papers/semantic?q=graph&year=2024&venue=acl")
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["total"] == 1
    assert payload["items"][0]["doc_id"] == "hash-1"
    assert payload["items"][0]["score_type"] == "hybrid"

    limited = client.get(
        "/api/papers/semantic?q=attention&top_n=1", headers={"Authorization": "Bearer secret-token"}
    )
    assert limited.status_code == 200
    assert limited.json()["total"] == 1

    app.state.index.papers[1].update(
        {
            "paper_title": "Graph",
            "_title_lc": "graph",
            "_venue": "Networks",
            "publication_venue": "Networks",
            "_search_lc": "graph networks bob",
        }
    )

    async def fake_phrase_hybrid_search(**kwargs):  # noqa: ANN001
        return [
            SimpleNamespace(
                doc_id="hash-1",
                score=0.9,
                score_type="hybrid",
                matched_chunk="graph networks",
                matched_field="summary",
                matched_template="simple",
                matched_chunk_type="paragraph",
                matched_lang="",
            ),
            SimpleNamespace(
                doc_id="hash-2",
                score=0.8,
                score_type="hybrid",
                matched_chunk="graph networks",
                matched_field="summary",
                matched_template="simple",
                matched_chunk_type="paragraph",
                matched_lang="",
            ),
        ]

    monkeypatch.setattr("deepresearch_flow.paper.search.hybrid_search", fake_phrase_hybrid_search)
    phrase = client.get("/api/papers/semantic", params={"q": '"graph networks"'})
    assert phrase.status_code == 200
    assert [item["doc_id"] for item in phrase.json()["items"]] == ["hash-1"]

    invalid = client.get("/api/papers/semantic?q=attention&venue=NeurIPS' OR 1=1")
    assert invalid.status_code == 400

    invalid_top_n = client.get("/api/papers/semantic?q=attention&top_n=not-an-int")
    assert invalid_top_n.status_code == 400

    invalid_year = client.get("/api/papers/semantic?q=attention&year=20xx")
    assert invalid_year.status_code == 400

    async def failing_call_embedding(
        base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None
    ):  # noqa: ANN001, ARG001
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", failing_call_embedding)
    failed = client.get("/api/papers/semantic?q=attention&top_n=5")
    assert failed.status_code == 502
    assert failed.json()["error"] == "Semantic search query embedding failed"
