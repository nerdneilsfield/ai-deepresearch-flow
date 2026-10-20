"""Black-box tests for admin write path, semantic optimize, and OAuth https rules."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from unittest import mock

import httpx
import pytest
from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.admin import create_admin_app
from deepresearch_flow.paper.snapshot.advanced.web_oauth import SearchWebOAuthConfig
from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.auth import McpGitHubOAuthConfig
from deepresearch_flow.paper.snapshot.push import RemoteConfig
from deepresearch_flow.paper.snapshot.push_semantic import optimize_remote_semantic
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db, recompute_facet_edges
from deepresearch_flow.paper.vector_store import (
    compute_group_fingerprint,
    open_store,
    read_all_chunks,
)
from deepresearch_flow.paper.snapshot.tests.test_admin_semantic import (
    _body,
    _chunk,
    _make_app,
)

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "snapshot.db"
    conn = sqlite3.connect(str(db_path))
    try:
        init_snapshot_db(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _paper(title: str, doi: str, keywords: list[str], tags: list[str], authors: list[str]) -> dict:
    return {
        "paper_title": title,
        "doi": doi,
        "paper_authors": authors,
        "publication_date": "2020-01",
        "keywords": keywords,
        "ai_generated_tags": tags,
        "source_hash": title,
        "templates": {"simple": {"paper_title": title, "summary": f"{title} summary"}},
    }


def _facet_edges(db_path: Path) -> set[tuple[int, int, int]]:
    conn = sqlite3.connect(str(db_path))
    try:
        return set(
            conn.execute("SELECT node_id_a, node_id_b, paper_count FROM facet_edge").fetchall()
        )
    finally:
        conn.close()


def _recomputed_facet_edges(db_path: Path) -> set[tuple[int, int, int]]:
    conn = sqlite3.connect(str(db_path))
    try:
        recompute_facet_edges(conn)
        conn.commit()
    finally:
        conn.close()
    return _facet_edges(db_path)


def test_facet_edge_matches_full_recompute_after_insert_overwrite_delete(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    client = TestClient(create_admin_app(snapshot_db=db_path, admin_token=TOKEN))

    p1 = _paper("One", "10.1/one", ["kw-a", "kw-b"], ["tag-x"], ["Alice", "Bob"])
    p2 = _paper("Two", "10.1/two", ["kw-a", "kw-c"], ["tag-x", "tag-y"], ["Alice"])
    p3 = _paper("Three", "10.1/three", ["kw-b", "kw-c"], ["tag-y"], ["Bob", "Carol"])
    resp = client.post("/papers", json={"papers": [p1, p2, p3]}, headers=HEADERS)
    assert resp.json()["added"] == 3
    ids = resp.json()["paper_ids"]
    assert _facet_edges(db_path) == _recomputed_facet_edges(db_path)

    replacement = _paper("Two", "10.1/two", ["kw-z"], ["tag-z"], ["Dave"])
    resp = client.post(
        "/papers", json={"papers": [replacement], "overwrite": True}, headers=HEADERS
    )
    assert resp.json()["updated"] == 1
    assert _facet_edges(db_path) == _recomputed_facet_edges(db_path)

    assert client.delete(f"/papers/{ids[0]}", headers=HEADERS).status_code == 200
    assert _facet_edges(db_path) == _recomputed_facet_edges(db_path)

    assert client.delete(f"/papers/{ids[2]}", headers=HEADERS).status_code == 200
    assert _facet_edges(db_path) == _recomputed_facet_edges(db_path)


@pytest.mark.anyio
async def test_reads_are_served_while_a_paper_batch_is_being_written(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    app = create_app(
        snapshot_db=db_path, static_base_url="", mcp_access_token="mcp-test", admin_token=TOKEN
    )
    from deepresearch_flow.paper.snapshot import admin as admin_module

    write_started = threading.Event()
    release_write = threading.Event()
    original_apply = admin_module.apply_paper_batch

    def blocked_apply(*args, **kwargs):  # noqa: ANN002, ANN003
        write_started.set()
        assert release_write.wait(timeout=10)
        return original_apply(*args, **kwargs)

    transport = httpx.ASGITransport(app=app)
    with mock.patch.object(admin_module, "apply_paper_batch", blocked_apply):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            write_task = asyncio.create_task(
                client.post(
                    "/api/v1/admin/papers",
                    json={"papers": [_paper("Slow", "10.1/slow", ["kw"], ["tag"], ["Al"])]},
                    headers=HEADERS,
                )
            )
            try:
                assert await asyncio.to_thread(write_started.wait, 10)
                read_resp = await asyncio.wait_for(client.get("/api/v1/stats"), timeout=5)
                assert not write_task.done()
            finally:
                release_write.set()
            write_resp = await write_task

    assert read_resp.status_code == 200
    assert write_resp.status_code == 200
    assert write_resp.json()["added"] == 1


def test_optimize_requires_auth(tmp_path: Path) -> None:
    client, _, _ = _make_app(tmp_path)
    assert client.post("/semantic/optimize").status_code == 401
    assert (
        client.post("/semantic/optimize", headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )


def test_optimize_returns_503_without_embed_db(tmp_path: Path) -> None:
    client = TestClient(create_admin_app(snapshot_db=_make_db(tmp_path), admin_token=TOKEN))
    resp = client.post("/semantic/optimize", headers=HEADERS)
    assert resp.status_code == 503


def test_optimize_keeps_rows_after_many_writes(tmp_path: Path) -> None:
    client, headers, embed_dir = _make_app(tmp_path)
    for doc in range(6):
        doc_id = f"d{doc}"
        resp = client.post(
            "/semantic/chunks/batch",
            json=_body(doc_id, "simple", [_chunk(doc_id, "simple", i) for i in range(3)]),
            headers=headers,
        )
        assert resp.status_code == 200
    before = sorted(row["id"] for row in read_all_chunks(open_store(embed_dir)))

    resp = client.post("/semantic/optimize", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    after = sorted(row["id"] for row in read_all_chunks(open_store(embed_dir)))
    assert after == before
    assert len(after) == 18


def test_optimize_remote_semantic_posts_with_bearer_and_raises_on_error() -> None:
    config = RemoteConfig(api_base_url="https://api.example.com", admin_token="secret")
    seen: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        seen["url"] = url
        seen["headers"] = kwargs["headers"]
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    with mock.patch("httpx.post", fake_post):
        optimize_remote_semantic(config)
    assert seen["url"] == "https://api.example.com/api/v1/admin/semantic/optimize"
    assert seen["headers"] == {"Authorization": "Bearer secret"}

    def failing_post(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(503, request=httpx.Request("POST", url))

    with mock.patch("httpx.post", failing_post):
        with pytest.raises(httpx.HTTPStatusError):
            optimize_remote_semantic(config)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com",
        "http://192.168.1.5:8080",
        "http://10.0.0.5",
        "http://[fd00::1]",
    ],
)
def test_oauth_configs_reject_http_outside_localhost(base_url: str) -> None:
    with pytest.raises(ValueError, match="must use https outside localhost"):
        McpGitHubOAuthConfig(
            public_base_url=base_url,
            client_id="id",
            client_secret="secret",
            allowed_github_user_ids=("1",),
        )
    with pytest.raises(ValueError, match="must use https outside localhost"):
        SearchWebOAuthConfig(
            public_base_url=base_url,
            client_id="id",
            client_secret="secret",
            allowed_github_user_ids=("1",),
        )


def test_manifest_endpoints_require_auth(tmp_path: Path) -> None:
    client, _, _ = _make_app(tmp_path)
    for path in ("/papers/manifest", "/semantic/manifest"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_papers_manifest_lists_paper_ids_with_source_hash(tmp_path: Path) -> None:
    client = TestClient(create_admin_app(snapshot_db=_make_db(tmp_path), admin_token=TOKEN))
    assert client.get("/papers/manifest", headers=HEADERS).json()["papers"] == {}

    papers = [
        _paper("One", "10.1/one", ["kw"], ["tag"], ["Al"]),
        _paper("Two", "10.1/two", ["kw"], ["tag"], ["Al"]),
    ]
    ids = client.post("/papers", json={"papers": papers}, headers=HEADERS).json()["paper_ids"]

    resp = client.get("/papers/manifest", headers=HEADERS)

    assert resp.status_code == 200
    assert resp.json()["papers"] == {ids[0]: "One", ids[1]: "Two"}


def test_semantic_manifest_lists_group_fingerprints(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    assert client.get("/semantic/manifest", headers=headers).json() == {"groups": []}

    shared = [_chunk("d1", "", i) for i in range(2)]
    tagged = [_chunk("d1", "simple", 0, content_hash="x")]
    for tag, chunks in (("", shared), ("simple", tagged)):
        resp = client.post("/semantic/chunks/batch", json=_body("d1", tag, chunks), headers=headers)
        assert resp.status_code == 200

    resp = client.get("/semantic/manifest", headers=headers)

    assert resp.status_code == 200
    assert sorted(resp.json()["groups"], key=lambda g: g["template_key"]) == [
        {
            "doc_id": "d1",
            "template_key": "_shared",
            "fingerprint": compute_group_fingerprint(
                [(c["id"], c["content_hash"]) for c in shared]
            ),
        },
        {
            "doc_id": "d1",
            "template_key": "simple",
            "fingerprint": compute_group_fingerprint(
                [(c["id"], c["content_hash"]) for c in tagged]
            ),
        },
    ]


def test_semantic_manifest_keeps_groups_distinct_when_ids_contain_tabs(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    for doc_id, tag in (("a\tb", "c"), ("a", "b\tc")):
        resp = client.post(
            "/semantic/chunks/batch",
            json=_body(doc_id, tag, [_chunk(doc_id, tag, 0)]),
            headers=headers,
        )
        assert resp.status_code == 200

    groups = client.get("/semantic/manifest", headers=headers).json()["groups"]

    assert sorted((g["doc_id"], g["template_key"]) for g in groups) == [
        ("a", "b\tc"),
        ("a\tb", "c"),
    ]


def test_semantic_batch_with_swapped_chunk_ids_is_not_skipped(tmp_path: Path) -> None:
    client, headers, embed_dir = _make_app(tmp_path)
    first = [_chunk("d1", "simple", i) for i in range(2)]
    assert (
        client.post(
            "/semantic/chunks/batch", json=_body("d1", "simple", first), headers=headers
        ).status_code
        == 200
    )
    swapped = [dict(first[0], id=first[1]["id"]), dict(first[1], id=first[0]["id"])]
    renamed = [dict(first[0], id="d1_simple_content_renamed"), first[1]]

    for chunks in (swapped, renamed):
        resp = client.post(
            "/semantic/chunks/batch", json=_body("d1", "simple", chunks), headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["skipped"] != len(chunks)
        stored = {row["id"]: row["content_hash"] for row in read_all_chunks(open_store(embed_dir))}
        assert stored == {chunk["id"]: chunk["content_hash"] for chunk in chunks}


def test_papers_manifest_lists_aliases(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    client = TestClient(create_admin_app(snapshot_db=db_path, admin_token=TOKEN))
    paper_id = client.post(
        "/papers",
        json={"papers": [_paper("One", "10.1/one", ["kw"], ["tag"], ["Al"])]},
        headers=HEADERS,
    ).json()["paper_ids"][0]

    resp = client.get("/papers/manifest", headers=HEADERS).json()

    assert resp["aliases"]
    assert set(resp["aliases"].values()) == {paper_id}


def test_semantic_manifest_returns_503_without_embed_db(tmp_path: Path) -> None:
    client = TestClient(create_admin_app(snapshot_db=_make_db(tmp_path), admin_token=TOKEN))
    assert client.get("/semantic/manifest", headers=HEADERS).status_code == 503


def test_reingesting_an_unchanged_group_is_skipped_without_writing(tmp_path: Path) -> None:
    client, headers, embed_dir = _make_app(tmp_path)
    body = _body("d1", "simple", [_chunk("d1", "simple", i) for i in range(3)])
    assert client.post("/semantic/chunks/batch", json=body, headers=headers).status_code == 200
    table = open_store(embed_dir).open_table("paper_chunks")
    version_before = table.version
    rows_before = sorted(read_all_chunks(open_store(embed_dir)), key=lambda row: row["id"])

    resp = client.post("/semantic/chunks/batch", json=body, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"received": 3, "inserted": 0, "updated": 0, "skipped": 3, "deleted": 0}
    reopened = open_store(embed_dir).open_table("paper_chunks")
    assert reopened.version == version_before
    rows_after = sorted(read_all_chunks(open_store(embed_dir)), key=lambda row: row["id"])
    assert rows_after == rows_before
