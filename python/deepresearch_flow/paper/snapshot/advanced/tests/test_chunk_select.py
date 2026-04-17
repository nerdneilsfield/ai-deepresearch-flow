from __future__ import annotations

import pytest

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk, select_chunks
from deepresearch_flow.paper.snapshot.advanced.errors import VectorStoreUnavailableError
from deepresearch_flow.paper.snapshot.advanced.fusion import FusedPaper
from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit


def _chunk(paper_id: str, chunk_id: str, chunk_type: str, chunk_index: int, score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id,
        paper_id=paper_id,
        dense_score=score,
        chunk_text=f"t-{chunk_id}",
        field_name="",
        template_tag="simple",
        chunk_type=chunk_type,
        chunk_index=chunk_index,
        lang="en",
        vector=(0.0,),
    )


class _FakeLance:
    def __init__(self, rows_by_paper: dict[str, list[dict]]):
        self.rows_by_paper = rows_by_paper
        self.calls: list[str] = []

    def open_table(self, name):
        return self

    def search(self, *args, **kwargs):
        return self

    def where(self, clause: str):
        self.calls.append(clause)
        self._current = []
        for paper_id, rows in self.rows_by_paper.items():
            if f"doc_id = '{paper_id}'" in clause:
                self._current = rows
                break
        return self

    def limit(self, n):
        self._current = self._current[:n]
        return self

    def to_list(self):
        return list(self._current)


def test_dense_chunk_picked_when_available() -> None:
    fused = [FusedPaper("p1", 0.02, paper_dense_score=0.9, paper_sparse_score=None)]
    dense = [_chunk("p1", "p1_a", "content", 2, 0.5), _chunk("p1", "p1_b", "content", 5, 0.9)]
    output = select_chunks(
        fused_papers=fused,
        dense_chunks=dense,
        lance_db=_FakeLance({}),
        max_papers=10,
    )
    assert len(output) == 1
    assert isinstance(output[0], SelectedChunk)
    assert output[0].chunk_id == "p1_b"


def test_sparse_only_fetches_abstract_from_lance() -> None:
    fused = [FusedPaper("p1", 0.01, paper_dense_score=None, paper_sparse_score=4.0)]
    lance = _FakeLance({
        "p1": [
            {"id": "p1_simple_content_3", "doc_id": "p1", "text": "body", "field_name": "simple/content", "template_tag": "simple", "chunk_type": "content", "chunk_index": 3, "lang": "en", "vector": [0.0]},
            {"id": "p1_simple_abstract_0", "doc_id": "p1", "text": "abs", "field_name": "simple/abstract", "template_tag": "simple", "chunk_type": "abstract", "chunk_index": 0, "lang": "en", "vector": [0.0]},
        ]
    })
    output = select_chunks(fused_papers=fused, dense_chunks=[], lance_db=lance, max_papers=10)
    assert output[0].chunk_type == "abstract"


def test_falls_back_to_index_zero() -> None:
    fused = [FusedPaper("p2", 0.01, None, 1.0)]
    lance = _FakeLance({
        "p2": [
            {"id": "p2_simple_content_5", "doc_id": "p2", "text": "x", "field_name": "simple/content", "template_tag": "simple", "chunk_type": "content", "chunk_index": 5, "lang": "en", "vector": [0.0]},
            {"id": "p2_simple_content_0", "doc_id": "p2", "text": "zero", "field_name": "simple/content", "template_tag": "simple", "chunk_type": "content", "chunk_index": 0, "lang": "en", "vector": [0.0]},
        ]
    })
    output = select_chunks(fused_papers=fused, dense_chunks=[], lance_db=lance, max_papers=10)
    assert output[0].chunk_index == 0


def test_lance_failure_raises() -> None:
    class _Bad:
        def open_table(self, name):
            raise RuntimeError("cannot open")

    with pytest.raises(VectorStoreUnavailableError):
        select_chunks(
            fused_papers=[FusedPaper("p2", 0.01, None, 1.0)],
            dense_chunks=[],
            lance_db=_Bad(),
            max_papers=10,
        )


def test_max_papers_truncates() -> None:
    fused = [
        FusedPaper(f"p{i}", 1.0 / (i + 1), paper_dense_score=0.5, paper_sparse_score=None)
        for i in range(10)
    ]
    dense = [_chunk(f"p{i}", f"p{i}_c0", "content", 0, 0.5) for i in range(10)]
    output = select_chunks(fused_papers=fused, dense_chunks=dense, lance_db=_FakeLance({}), max_papers=3)
    assert len(output) == 3
