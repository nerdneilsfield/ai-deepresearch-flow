from __future__ import annotations

import asyncio

from deepresearch_flow.paper.search import (
    SearchHit,
    aggregate_by_doc_id,
    hybrid_search,
    rank_keyword_rows,
    vector_hits_to_search_hits,
    reciprocal_rank_fusion,
)
from deepresearch_flow.paper.reranker import RerankResult


def test_rrf_single_list() -> None:
    ranked = ["doc_a", "doc_b", "doc_c"]
    scores = reciprocal_rank_fusion([ranked], k=60)
    assert scores["doc_a"] > scores["doc_b"] > scores["doc_c"]


def test_rrf_two_lists_overlap() -> None:
    vector_ranked = ["doc_a", "doc_b", "doc_c"]
    keyword_ranked = ["doc_b", "doc_d", "doc_a"]
    scores = reciprocal_rank_fusion([vector_ranked, keyword_ranked], k=60)
    assert scores["doc_a"] > scores["doc_c"]
    assert scores["doc_b"] > scores["doc_d"]


def test_rrf_empty_lists() -> None:
    scores = reciprocal_rank_fusion([], k=60)
    assert scores == {}


def test_aggregate_by_doc_id_picks_best_chunk() -> None:
    hits = [
        SearchHit(
            doc_id="doc1",
            chunk_text="chunk A",
            score=0.9,
            field_name="simple/summary",
            template_tag="simple",
            chunk_type="abstract",
            lang="",
        ),
        SearchHit(
            doc_id="doc1",
            chunk_text="chunk B",
            score=0.7,
            field_name="deep_read/findings",
            template_tag="deep_read",
            chunk_type="content",
            lang="",
        ),
        SearchHit(
            doc_id="doc2",
            chunk_text="chunk C",
            score=0.8,
            field_name="title",
            template_tag="",
            chunk_type="title",
            lang="",
        ),
    ]
    aggregated = aggregate_by_doc_id(hits)
    assert len(aggregated) == 2
    doc1 = next(h for h in aggregated if h.doc_id == "doc1")
    assert doc1.chunk_text == "chunk A"
    assert doc1.score == 0.9


def test_aggregate_translated_md_preserves_lang() -> None:
    hits = [
        SearchHit(
            doc_id="doc1",
            chunk_text="中文摘要",
            score=0.95,
            field_name="translated_md",
            template_tag="",
            chunk_type="translated_md",
            lang="zh",
        ),
    ]
    aggregated = aggregate_by_doc_id(hits)
    assert aggregated[0].lang == "zh"


def test_vector_hits_use_cosine_distance_directly() -> None:
    hits = vector_hits_to_search_hits(
        [
            {
                "doc_id": "doc1",
                "text": "chunk",
                "_distance": 0.25,
                "field_name": "title",
                "template_tag": "",
                "chunk_type": "title",
                "lang": "",
            }
        ]
    )
    assert hits[0].score == 0.75


def test_rank_keyword_rows_scores_phrase_and_doc_best_hit() -> None:
    rows = [
        {
            "doc_id": "doc-a",
            "title": "Attention Mechanism Survey",
            "text": "Transformer attention mechanism overview",
            "authors": "Alice",
            "venue": "NeurIPS",
            "tags": "attention,transformer",
        },
        {
            "doc_id": "doc-b",
            "title": "Random Paper",
            "text": "attention appears once",
            "authors": "Bob",
            "venue": "ICML",
            "tags": "",
        },
        {
            "doc_id": "doc-a",
            "title": "Attention Mechanism Survey",
            "text": "another matching chunk",
            "authors": "Alice",
            "venue": "NeurIPS",
            "tags": "",
        },
    ]

    ranked = rank_keyword_rows(rows, "attention mechanism", limit=5)
    assert ranked[0] == "doc-a"
    assert ranked[1] == "doc-b"


def test_hybrid_search_uses_reranker_with_keyword_only_candidates(monkeypatch) -> None:
    class DummyReranker:
        async def rerank(self, query, documents, *, top_n, client):  # noqa: ANN001
            assert query == "attention mechanism"
            assert documents == ["vector chunk", "keyword-only summary"]
            assert top_n == 2
            return RerankResult(indices=[1, 0], scores=[0.91, 0.62])

    monkeypatch.setattr(
        "deepresearch_flow.paper.vector_store.query_vector",
        lambda db, query_vector, top_k=50, where=None: [  # noqa: ARG005
            {
                "doc_id": "doc-vector",
                "text": "vector chunk",
                "_distance": 0.2,
                "field_name": "summary",
                "template_tag": "simple",
                "chunk_type": "abstract",
                "lang": "",
            }
        ],
    )

    async def _run():
        return await hybrid_search(
            query_vector=[0.1, 0.2],
            query_text="attention mechanism",
            vector_store_db=object(),
            keyword_search_fn=lambda q, limit=30: ["doc-vector", "doc-keyword"],  # noqa: ARG005
            reranker=DummyReranker(),
            vector_top_k=5,
            keyword_top_k=5,
            rerank_top_n=2,
            hybrid=True,
            document_text_resolver=lambda doc_id: {
                "doc-keyword": "keyword-only summary",
            }.get(doc_id, doc_id),
            client=object(),
        )

    results = asyncio.run(_run())
    assert [item.doc_id for item in results] == ["doc-keyword", "doc-vector"]
    assert [item.score_type for item in results] == ["rerank", "rerank"]
    assert results[0].matched_chunk == ""
    assert results[1].matched_chunk == "vector chunk"
