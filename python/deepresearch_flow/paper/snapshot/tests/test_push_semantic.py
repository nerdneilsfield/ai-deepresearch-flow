from __future__ import annotations

import json
from unittest import mock

import httpx
import pytest

from deepresearch_flow.paper.snapshot.push import RemoteConfig
from deepresearch_flow.paper.snapshot.push_semantic import (
    SemanticPushError,
    group_chunks_for_push,
    push_semantic_chunks,
    write_error_report,
)
from deepresearch_flow.paper.vector_store import decode_vector_b64


def _make_chunk(doc_id: str, template_tag: str, idx: int, *, text: str = "test") -> dict:
    return {
        "id": f"{doc_id}_{template_tag or '_shared'}_content_{idx}",
        "doc_id": doc_id,
        "source_path": "test.md",
        "template_tag": template_tag,
        "chunk_type": "content",
        "chunk_index": idx,
        "field_name": "summary",
        "lang": "",
        "text": text,
        "content_hash": f"hash_{idx}",
        "vector": [0.1, 0.2, 0.3, 0.4],
        "title": "Test",
        "year": 2024,
        "authors": "A",
        "venue": "V",
        "tags": "t",
    }


def test_group_single_group_one_part() -> None:
    chunks = [_make_chunk("doc1", "simple", i) for i in range(5)]
    batches = group_chunks_for_push(chunks, max_rows=100, max_payload_bytes=16_000_000)
    assert len(batches) == 1
    batch = batches[0]
    assert batch["group"]["doc_id"] == "doc1"
    assert batch["group"]["part_index"] == 0
    assert batch["group"]["part_count"] == 1
    assert batch["group"]["is_final_part"] is True
    assert len(batch["chunks"]) == 5
    assert "vector_b64" in batch["chunks"][0]
    assert "vector_dim" in batch["chunks"][0]
    assert "vector" not in batch["chunks"][0]
    decoded = decode_vector_b64(batch["chunks"][0]["vector_b64"], batch["chunks"][0]["vector_dim"])
    assert len(decoded) == 4


def test_group_large_group_splits() -> None:
    chunks = [_make_chunk("doc1", "simple", i) for i in range(20)]
    batches = group_chunks_for_push(chunks, max_rows=5, max_payload_bytes=16_000_000)
    assert len(batches) == 4
    for i, batch in enumerate(batches):
        assert batch["group"]["doc_id"] == "doc1"
        assert batch["group"]["part_index"] == i
        assert batch["group"]["part_count"] == 4
        assert batch["group"]["is_final_part"] == (i == 3)


def test_group_splits_by_payload_size_not_just_rows() -> None:
    big_chunks = [_make_chunk("doc1", "simple", i, text="x" * 4_000_000) for i in range(5)]
    batches = group_chunks_for_push(big_chunks, max_rows=100, max_payload_bytes=16_000_000)
    assert len(batches) > 1
    for batch in batches:
        assert batch["group"]["doc_id"] == "doc1"
        assert batch["group"]["part_count"] == len(batches)


def test_group_multiple_groups_separate_batches() -> None:
    chunks = [
        *[_make_chunk("doc1", "simple", i) for i in range(3)],
        *[_make_chunk("doc2", "", i) for i in range(2)],
    ]
    batches = group_chunks_for_push(chunks, max_rows=100, max_payload_bytes=16_000_000)
    assert len(batches) == 2
    assert {batch["group"]["doc_id"] for batch in batches} == {"doc1", "doc2"}


def test_push_sends_requests_and_accumulates_stats() -> None:
    chunks = [_make_chunk("doc1", "simple", i) for i in range(3)]
    index_meta = {"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}

    with mock.patch("deepresearch_flow.paper.snapshot.push_semantic.httpx.Client") as mock_cls:
        mock_client = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"received": 3, "inserted": 3, "updated": 0, "skipped": 0, "deleted": 0}
        mock_resp.raise_for_status = mock.MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_cls.return_value = mock_client

        config = RemoteConfig(api_base_url="http://localhost", admin_token="tok")
        stats = push_semantic_chunks(chunks, index_meta, config)
        assert stats.inserted == 3
        assert stats.batches_sent == 1

        call_body = mock_client.post.call_args.kwargs["json"]
        assert "index_meta" in call_body
        assert "group" in call_body
        assert "chunks" in call_body
        assert "vector_b64" in call_body["chunks"][0]


def test_push_retries_transport_errors_then_succeeds() -> None:
    chunks = [_make_chunk("doc1", "simple", i) for i in range(2)]
    index_meta = {"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}

    with mock.patch("deepresearch_flow.paper.snapshot.push_semantic.httpx.Client") as mock_cls:
        mock_client = mock.MagicMock()
        retry_error = httpx.RemoteProtocolError("Server disconnected")
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"received": 2, "inserted": 2, "updated": 0, "skipped": 0, "deleted": 0}
        mock_resp.raise_for_status = mock.MagicMock()
        mock_client.post.side_effect = [retry_error, mock_resp]
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_cls.return_value = mock_client

        config = RemoteConfig(api_base_url="http://localhost", admin_token="tok")
        stats = push_semantic_chunks(
            chunks,
            index_meta,
            config,
            retries=1,
            retry_backoff_seconds=0,
        )

    assert stats.inserted == 2
    assert stats.batches_sent == 1
    assert not stats.errors
    assert mock_client.post.call_count == 2


def test_push_failure_carries_batch_metadata_and_report(tmp_path) -> None:
    chunks = [_make_chunk("doc1", "simple", i) for i in range(2)]
    index_meta = {"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}

    with mock.patch("deepresearch_flow.paper.snapshot.push_semantic.httpx.Client") as mock_cls:
        mock_client = mock.MagicMock()
        mock_client.post.side_effect = httpx.RemoteProtocolError("Server disconnected")
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_cls.return_value = mock_client

        config = RemoteConfig(api_base_url="http://localhost", admin_token="tok")
        with pytest.raises(SemanticPushError) as excinfo:
            push_semantic_chunks(
                chunks,
                index_meta,
                config,
                retries=1,
                retry_backoff_seconds=0,
            )

    failure = excinfo.value.failure
    assert failure["doc_id"] == "doc1"
    assert failure["template_tag"] == "simple"
    assert failure["chunk_count"] == 2
    assert failure["attempts"] == 2
    assert "Server disconnected" in failure["error"]

    report_path = tmp_path / "push-semantic-errors.json"
    write_error_report(excinfo.value.stats.errors, report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report[0]["doc_id"] == "doc1"
    assert report[0]["request"]["group"]["doc_id"] == "doc1"
