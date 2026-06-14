from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
import unittest

from hypothesis import given, settings, strategies as st
import pytest

from deepresearch_flow.paper.snapshot.auth import JsonOAuthClientCache

COLLECTION = "mcp-oauth-proxy-clients"


class TestJsonOAuthClientCache(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.tmpdir.name) / "mcp-oauth-clients.json"
        self.collection = COLLECTION

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    async def test_persistent_client_can_be_read_after_cache_reopen(self) -> None:
        first = JsonOAuthClientCache(self.cache_path)
        await first.put(
            "client-a",
            {"client_id": "client-a", "redirect_uris": ["https://example.test/callback"]},
            collection=self.collection,
        )

        second = JsonOAuthClientCache(self.cache_path)
        restored = await second.get("client-a", collection=self.collection)

        self.assertEqual(restored["client_id"], "client-a")
        self.assertEqual(restored["redirect_uris"], ["https://example.test/callback"])

    async def test_persistent_client_reads_from_memory_after_file_changes(self) -> None:
        cache = JsonOAuthClientCache(self.cache_path)
        await cache.put(
            "client-a",
            {"client_id": "client-a", "redirect_uris": ["https://example.test/callback"]},
            collection=self.collection,
        )
        self.cache_path.unlink()

        restored = await cache.get("client-a", collection=self.collection)

        self.assertEqual(restored["client_id"], "client-a")

    async def test_transient_collections_are_not_written_to_json_file(self) -> None:
        cache = JsonOAuthClientCache(self.cache_path)
        await cache.put("state-a", {"state": "state-a"}, collection="mcp-oauth-transactions")

        reopened = JsonOAuthClientCache(self.cache_path)
        restored = await reopened.get("state-a", collection="mcp-oauth-transactions")

        self.assertIsNone(restored)


if __name__ == "__main__":
    unittest.main()


_KEY_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
_PATH_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


def _client_payloads() -> st.SearchStrategy[dict[str, object]]:
    client_ids = st.text(alphabet=_KEY_CHARS, min_size=1, max_size=24)
    uri_paths = st.lists(
        st.text(alphabet=_PATH_CHARS, min_size=1, max_size=12), min_size=1, max_size=4
    )
    return st.builds(
        lambda client_id, paths: {
            "client_id": client_id,
            "redirect_uris": [f"https://example.test/{path}" for path in paths],
        },
        client_ids,
        uri_paths,
    )


def _ops() -> st.SearchStrategy[tuple[object, ...]]:
    keys = st.text(alphabet=_KEY_CHARS, min_size=1, max_size=12)
    return st.one_of(
        st.tuples(st.just("put"), keys, _client_payloads()),
        st.tuples(st.just("delete"), keys),
        st.tuples(st.just("reopen")),
    )


@pytest.mark.fuzz_fast
@settings(max_examples=80, deadline=None)
@given(st.lists(_ops(), min_size=1, max_size=30))
def test_persistent_cache_matches_public_put_delete_reopen_model(
    ops: list[tuple[object, ...]],
) -> None:
    async def scenario(path: Path) -> None:
        cache = JsonOAuthClientCache(path)
        expected: dict[str, dict[str, object]] = {}
        seen: set[str] = set()

        for op in ops:
            kind = op[0]
            if kind == "put":
                key = str(op[1])
                value = dict(op[2])  # type: ignore[arg-type]
                await cache.put(key, value, collection=COLLECTION)
                expected[key] = value
                seen.add(key)
            elif kind == "delete":
                key = str(op[1])
                deleted = await cache.delete(key, collection=COLLECTION)
                assert deleted is (key in expected)
                expected.pop(key, None)
                seen.add(key)
            elif kind == "reopen":
                cache = JsonOAuthClientCache(path)

            for key in sorted(seen):
                assert await cache.get(key, collection=COLLECTION) == expected.get(key)

        reopened = JsonOAuthClientCache(path)
        for key in sorted(seen):
            assert await reopened.get(key, collection=COLLECTION) == expected.get(key)

    with tempfile.TemporaryDirectory() as tmpdir:
        asyncio.run(scenario(Path(tmpdir) / "mcp-oauth-clients.json"))


@pytest.mark.fault
def test_cache_starts_empty_when_persistent_file_is_malformed() -> None:
    async def scenario(path: Path) -> None:
        path.write_text("{not-json", encoding="utf-8")
        cache = JsonOAuthClientCache(path)
        assert await cache.get("missing-client", collection=COLLECTION) is None

    with tempfile.TemporaryDirectory() as tmpdir:
        asyncio.run(scenario(Path(tmpdir) / "mcp-oauth-clients.json"))
