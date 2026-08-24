from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from deepresearch_flow.pipeline.formal_gc import collect_unreferenced_formal_resources
from deepresearch_flow.pipeline.publication_store import (
    FormalStorePage,
    LocalFormalStore,
    MirroredFormalStore,
    WebDavFormalStore,
)
from deepresearch_flow.pipeline.publication_models import PublicationError
from deepresearch_flow.pipeline.state import PipelineState
from deepresearch_flow.paper.snapshot.publication import open_snapshot_connection


def _empty_snapshot(path: Path) -> None:
    connection = open_snapshot_connection(path)
    connection.commit()
    connection.close()


def _receipt(path: Path, job_id: str) -> None:
    connection = open_snapshot_connection(path)
    connection.execute(
        "INSERT INTO pipeline_publication_receipt(job_id,bundle_digest,paper_id,published_at) "
        "VALUES(?,?,?,?)",
        (job_id, "0" * 64, job_id, "2030-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()


def test_formal_gc_removes_only_unreferenced_content_addressed_files_with_bound(
    tmp_path: Path,
) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    referenced = b"referenced"
    orphan_a = b"orphan-a"
    orphan_b = b"orphan-b"
    orphan_summary = b"orphan-summary"
    referenced_path = f"pdf/{hashlib.sha256(referenced).hexdigest()}.pdf"
    orphan_a_path = f"pdf/{hashlib.sha256(orphan_a).hexdigest()}.pdf"
    orphan_b_path = f"pdf/{hashlib.sha256(orphan_b).hexdigest()}.pdf"
    orphan_summary_path = (
        f"summary/paper-id/simple/{hashlib.sha256(orphan_summary).hexdigest()}.json"
    )
    for path, data in (
        (referenced_path, referenced),
        (orphan_a_path, orphan_a),
        (orphan_b_path, orphan_b),
        (orphan_summary_path, orphan_summary),
    ):
        store.put(path, data)
    unrelated = store.root / "notes" / f"{hashlib.sha256(b'unrelated').hexdigest()}.txt"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unrelated")

    manifest = {
        "version": 1,
        "job_id": "job",
        "bundle_digest": "0" * 64,
        "resources": [
            {
                "path": referenced_path,
                "digest": hashlib.sha256(referenced).hexdigest(),
                "size": len(referenced),
            }
        ],
    }
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    _receipt(snapshot, "job")
    first = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        manifests=[manifest],
        limit=1,
        grace_seconds=0,
    )
    assert len(first.deleted) == 1
    assert (store.root / referenced_path).exists()
    second = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        manifests=[manifest],
        limit=10,
        grace_seconds=0,
    )
    assert set(first.deleted) | set(second.deleted) == {
        orphan_a_path,
        orphan_b_path,
        orphan_summary_path,
    }
    assert not (store.root / orphan_a_path).exists()
    assert not (store.root / orphan_b_path).exists()
    assert not (store.root / orphan_summary_path).exists()
    assert unrelated.exists()


def test_local_formal_store_page_is_budgeted_resumable_and_symlink_safe(
    tmp_path: Path,
) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    expected: set[str] = set()
    for index in range(5):
        data = f"page-orphan-{index}".encode()
        relative = f"pdf/{hashlib.sha256(data).hexdigest()}.pdf"
        store.put(relative, data)
        expected.add(relative)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    link = store.root / "pdf" / "link.pdf"
    link.symlink_to(outside)

    cursor: str | None = None
    observed: set[str] = set()
    for _ in range(30):
        page = store.list_content_addressed_page(
            max_items=1,
            after=cursor,
            content_addressed_only=True,
            inspection_limit=2,
        )
        assert page.inspected <= 2
        assert len(page.items) <= 1
        observed.update(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert observed == expected
    with pytest.raises(PublicationError):
        store.list_content_addressed_page(after="v1l.invalid")


def test_local_formal_gc_resets_unrecoverable_cursor_after_store_restart(
    tmp_path: Path,
) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    data = [b"first restart orphan", b"second restart orphan"]
    paths = tuple(
        f"pdf/{hashlib.sha256(value).hexdigest()}.pdf" for value in data
    )
    for path, value in zip(paths, data):
        store.put(path, value)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)

    first = collect_unreferenced_formal_resources(
        store, snapshot_db=snapshot, limit=1, grace_seconds=0
    )
    assert len(first.deleted) == 1
    assert first.next_cursor is not None

    restarted = LocalFormalStore(store.root)
    recovered = collect_unreferenced_formal_resources(
        restarted,
        snapshot_db=snapshot,
        limit=1,
        grace_seconds=0,
        cursor=first.next_cursor,
    )

    assert recovered.deleted == ()
    assert recovered.warning is not None
    assert recovered.next_cursor is None
    assert any((restarted.root / path).exists() for path in paths)


def test_local_formal_store_page_converges_after_directory_changes(
    tmp_path: Path,
) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    removed_data = b"removed during listing"
    retained_data = b"retained during listing"
    removed = f"pdf/{hashlib.sha256(removed_data).hexdigest()}.pdf"
    retained = f"pdf/{hashlib.sha256(retained_data).hexdigest()}.pdf"
    store.put(removed, removed_data)
    store.put(retained, retained_data)
    disappearing = store.root / "gone" / "nested"
    disappearing.mkdir(parents=True)
    (disappearing / "note.txt").write_text("gone", encoding="utf-8")

    first = store.list_content_addressed_page(
        max_items=1,
        content_addressed_only=True,
        inspection_limit=2,
    )
    assert first.next_cursor is not None
    removed_path = store.root / removed
    removed_path.unlink()
    (disappearing / "note.txt").unlink()
    disappearing.rmdir()
    (store.root / "gone").rmdir()
    added_data = b"added during listing"
    added = f"pdf/{hashlib.sha256(added_data).hexdigest()}.pdf"
    store.put(added, added_data)

    observed = set(first.items)
    cursor = first.next_cursor
    for _ in range(30):
        page = store.list_content_addressed_page(
            max_items=1,
            after=cursor,
            content_addressed_only=True,
            inspection_limit=2,
        )
        observed.update(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert retained in observed
    assert removed not in observed

    fresh_observed: set[str] = set()
    fresh_cursor: str | None = None
    for _ in range(30):
        page = store.list_content_addressed_page(
            max_items=1,
            after=fresh_cursor,
            content_addressed_only=True,
            inspection_limit=2,
        )
        fresh_observed.update(page.items)
        fresh_cursor = page.next_cursor
        if fresh_cursor is None:
            break
    assert added in fresh_observed


def test_local_formal_store_new_scan_abandons_previous_cursor_safely(
    tmp_path: Path,
) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    for index in range(3):
        data = f"abandoned-page-{index}".encode()
        store.put(f"pdf/{hashlib.sha256(data).hexdigest()}.pdf", data)

    abandoned = store.list_content_addressed_page(
        max_items=1,
        content_addressed_only=True,
        inspection_limit=2,
    )
    assert abandoned.next_cursor is not None
    restarted_scan = store.list_content_addressed_page(
        max_items=1,
        content_addressed_only=True,
        inspection_limit=2,
    )

    with pytest.raises(PublicationError):
        store.list_content_addressed_page(
            max_items=1,
            after=abandoned.next_cursor,
            content_addressed_only=True,
            inspection_limit=2,
        )
    assert restarted_scan.items


def test_local_formal_store_invalid_cursor_discards_active_scan_safely(
    tmp_path: Path,
) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    payload = b"invalid cursor cleanup"
    store.put(f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf", payload)
    page = store.list_content_addressed_page(
        max_items=1,
        content_addressed_only=True,
        inspection_limit=1,
    )
    assert page.next_cursor is not None

    with pytest.raises(PublicationError):
        store.list_content_addressed_page(after="v1l.invalid")
    with pytest.raises(PublicationError):
        store.list_content_addressed_page(after=page.next_cursor)


def test_webdav_formal_gc_uses_explicit_list_read_delete_capability(
    tmp_path: Path,
) -> None:
    class FakeWebDav:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        def list(
            self,
            _prefix: str = "",
            *,
            max_items: int | None = None,
            after: str | None = None,
        ) -> tuple[str, ...]:
            values = sorted(
                path for path in self.files if after is None or path > after.rstrip("/")
            )
            return tuple(values if max_items is None else values[:max_items])

        def download(self, path: str) -> bytes:
            return self.files[path]

        def delete(self, path: str) -> None:
            self.files.pop(path, None)

    remote = FakeWebDav()
    store = WebDavFormalStore(remote)
    data = b"remote orphan"
    path = f"pdf/{hashlib.sha256(data).hexdigest()}.pdf"
    remote.files[path] = data

    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)

    result = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        limit=1,
        grace_seconds=0,
    )
    assert result.deleted == (path,)
    assert remote.files == {}


def test_webdav_formal_gc_reports_partial_delete_failure_without_overclaiming(
    tmp_path: Path,
) -> None:
    class PartialWebDav:
        def __init__(self, failing_path: str) -> None:
            self.files: dict[str, bytes] = {}
            self.failing_path = failing_path

        def list(
            self,
            _prefix: str = "",
            *,
            max_items: int | None = None,
            after: str | None = None,
        ) -> tuple[str, ...]:
            values = sorted(
                path for path in self.files if after is None or path > after.rstrip("/")
            )
            return tuple(values if max_items is None else values[:max_items])

        def download(self, path: str) -> bytes:
            return self.files[path]

        def delete(self, path: str) -> None:
            if path == self.failing_path:
                raise OSError("remote credentials must not appear in warning")
            self.files.pop(path, None)

    good = b"good remote orphan"
    bad = b"bad remote orphan"
    good_path = f"pdf/{hashlib.sha256(good).hexdigest()}.pdf"
    bad_path = f"pdf/{hashlib.sha256(bad).hexdigest()}.pdf"
    remote = PartialWebDav(bad_path)
    remote.files.update({good_path: good, bad_path: bad})
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)

    result = collect_unreferenced_formal_resources(
        WebDavFormalStore(remote),
        snapshot_db=snapshot,
        limit=10,
        grace_seconds=0,
    )

    assert result.deleted == (good_path,)
    assert result.warning is not None
    assert "remote credentials" not in result.warning
    assert bad_path in remote.files


def test_webdav_formal_store_does_not_traverse_outside_prefix() -> None:
    class PrefixListing:
        def list(
            self,
            prefix: str = "",
            *,
            max_items: int | None = None,
            after: str | None = None,
        ) -> tuple[str, ...]:
            if prefix == "published":
                values = ("published/pdf/", "private/")
            elif prefix == "published/pdf":
                values = ("published/pdf/object.txt",)
            else:
                raise AssertionError(f"outside prefix was traversed: {prefix}")
            values = tuple(
                value
                for value in values
                if after is None or value.rstrip("/") > after.rstrip("/")
            )
            return values if max_items is None else values[:max_items]

    files = WebDavFormalStore(PrefixListing(), prefix="published").list_content_addressed_files()

    assert files == ("pdf/object.txt",)


def test_webdav_formal_store_rejects_parent_traversal_in_listing() -> None:
    class UnsafeListing:
        def list(
            self,
            _prefix: str = "",
            *,
            max_items: int | None = None,
            after: str | None = None,
        ) -> tuple[str, ...]:
            del after
            values = ("published/../secret/",)
            return values if max_items is None else values[:max_items]

    with pytest.raises(ValueError, match="traversal"):
        WebDavFormalStore(UnsafeListing(), prefix="published").list_content_addressed_files()


def test_webdav_gc_fails_closed_without_bounded_listing_capability(
    tmp_path: Path,
) -> None:
    payload = b"legacy listing must remain safe"
    relative = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"

    class LegacyListing:
        def __init__(self) -> None:
            self.files = {relative: payload}

        def list(self, _prefix: str = "") -> tuple[str, ...]:
            return tuple(self.files)

        def download(self, path: str) -> bytes:
            return self.files[path]

        def delete(self, path: str) -> None:
            self.files.pop(path, None)

    remote = LegacyListing()
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)

    result = collect_unreferenced_formal_resources(
        WebDavFormalStore(remote),
        snapshot_db=snapshot,
        limit=1,
        grace_seconds=0,
    )

    assert result.deleted == ()
    assert result.warning is not None
    assert "bounded" in result.warning
    assert remote.files == {relative: payload}


def test_webdav_cursor_uses_full_completed_collection_path_for_deep_siblings(
    tmp_path: Path,
) -> None:
    payload = b"sibling after empty deep collection"
    orphan = f"summary/zeta/simple/{hashlib.sha256(payload).hexdigest()}.json"

    class TreeListing:
        def __init__(self) -> None:
            self.collections = {
                "": ("summary/",),
                "summary": ("summary/zeta/",),
                "summary/zeta": ("summary/zeta/same/", "summary/zeta/simple/"),
                "summary/zeta/same": ("summary/zeta/same/readme.txt",),
                "summary/zeta/simple": (orphan,),
            }

        def list(
            self,
            prefix: str = "",
            *,
            max_items: int | None = None,
            after: str | None = None,
        ) -> tuple[str, ...]:
            values = self.collections.get(prefix, ())
            values = tuple(
                value
                for value in values
                if after is None or value.rstrip("/") > after.rstrip("/")
            )
            return values if max_items is None else values[:max_items]

        def download(self, path: str) -> bytes:
            assert path == orphan
            return payload

        def delete(self, path: str) -> None:
            assert path == orphan
            self.collections["summary/zeta/simple"] = ()

    remote = TreeListing()
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    cursor: str | None = None
    seen_cursors: list[str] = []
    deleted: set[str] = set()
    for _ in range(12):
        result = collect_unreferenced_formal_resources(
            WebDavFormalStore(remote),
            snapshot_db=snapshot,
            limit=1,
            grace_seconds=0,
            cursor=cursor,
        )
        deleted.update(result.deleted)
        cursor = result.next_cursor
        if cursor is not None:
            assert cursor not in seen_cursors
            seen_cursors.append(cursor)
        if orphan in deleted:
            break

    assert deleted == {orphan}
    assert len(seen_cursors) >= 2


def test_formal_gc_missing_snapshot_fails_closed_without_deletion(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    data = b"must remain until Snapshot exists"
    relative = f"pdf/{hashlib.sha256(data).hexdigest()}.pdf"
    store.put(relative, data)

    result = collect_unreferenced_formal_resources(
        store,
        snapshot_db=tmp_path / "not-created.sqlite3",
        limit=1,
        grace_seconds=0,
    )

    assert result.deleted == ()
    assert result.warning is not None
    assert store.read(relative) == data


def test_formal_gc_missing_reference_table_fails_closed_without_deletion(
    tmp_path: Path,
) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    data = b"broken Snapshot remains safe"
    relative = f"pdf/{hashlib.sha256(data).hexdigest()}.pdf"
    store.put(relative, data)
    snapshot = tmp_path / "broken.sqlite3"
    connection = sqlite3.connect(snapshot)
    connection.execute("CREATE TABLE paper (pdf_content_hash TEXT)")
    connection.commit()
    connection.close()

    result = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        limit=1,
        grace_seconds=0,
    )

    assert result.deleted == ()
    assert result.warning is not None
    assert store.read(relative) == data


def test_receipt_backed_indexing_manifest_protects_formal_resource(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    referenced = b"receipt-backed indexing resource"
    orphan = b"unreferenced"
    referenced_path = f"pdf/{hashlib.sha256(referenced).hexdigest()}.pdf"
    orphan_path = f"pdf/{hashlib.sha256(orphan).hexdigest()}.pdf"
    store.put(referenced_path, referenced)
    store.put(orphan_path, orphan)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    _receipt(snapshot, "indexing-job")
    manifest = {
        "version": 1,
        "job_id": "indexing-job",
        "bundle_digest": "0" * 64,
        "resources": [
            {
                "path": referenced_path,
                "digest": hashlib.sha256(referenced).hexdigest(),
                "size": len(referenced),
            }
        ],
    }

    result = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        manifests=[manifest],
        limit=10,
        grace_seconds=0,
    )

    assert result.deleted == (orphan_path,)
    assert store.read(referenced_path) == referenced


def test_receipt_without_manifest_fails_closed_without_deletion(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    data = b"receipt requires manifest"
    relative = f"pdf/{hashlib.sha256(data).hexdigest()}.pdf"
    store.put(relative, data)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    _receipt(snapshot, "missing-manifest")

    result = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        manifests=[],
        limit=1,
        grace_seconds=0,
    )

    assert result.deleted == ()
    assert result.warning is not None
    assert store.read(relative) == data


def test_snapshot_hash_and_resource_columns_protect_current_resources(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    pdf = b"snapshot pdf"
    source = b"# snapshot source"
    summary = b"snapshot summary"
    translated = b"# translated"
    paths = {
        "pdf": f"pdf/{hashlib.sha256(pdf).hexdigest()}.pdf",
        "source": f"md/{hashlib.sha256(source).hexdigest()}.md",
        "summary": "summary/paper-1/simple/" + hashlib.sha256(summary).hexdigest() + ".json",
        "translated": f"md_translate/en/{hashlib.sha256(translated).hexdigest()}.md",
    }
    for key, data in (("pdf", pdf), ("source", source), ("summary", summary), ("translated", translated)):
        store.put(paths[key], data)
    snapshot = tmp_path / "snapshot.sqlite3"
    connection = open_snapshot_connection(snapshot)
    connection.execute(
        "INSERT INTO paper(paper_id,paper_key,paper_key_type,title,year,month,publication_date,"
        "venue,preferred_summary_template,summary_preview,pdf_content_hash,source_md_content_hash) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "paper-1",
            "paper-1",
            "bib",
            "Snapshot paper",
            "2026",
            "01",
            "2026-01-01",
            "Journal",
            "simple",
            "summary",
            hashlib.sha256(pdf).hexdigest(),
            hashlib.sha256(source).hexdigest(),
        ),
    )
    connection.execute(
        "INSERT INTO paper_summary(paper_id,template_tag,resource_path,content_hash) VALUES(?,?,?,?)",
        ("paper-1", "simple", paths["summary"], hashlib.sha256(summary).hexdigest()),
    )
    connection.execute(
        "INSERT INTO paper_translation(paper_id,lang,md_content_hash) VALUES(?,?,?)",
        ("paper-1", "en", hashlib.sha256(translated).hexdigest()),
    )
    connection.commit()
    connection.close()

    result = collect_unreferenced_formal_resources(
        store, snapshot_db=snapshot, limit=10, grace_seconds=0
    )

    assert result.deleted == ()
    assert all(store.read(path) for path in paths.values())


def test_bounded_gc_cursor_converges_past_large_referenced_prefix(tmp_path: Path) -> None:
    class PagedStore(LocalFormalStore):
        list_content_addressed_page = None

        def list_content_addressed_files(
            self, *, max_items: int | None = None, after: str | None = None
        ) -> tuple[str, ...]:
            values = list(super().list_content_addressed_files())
            if after is not None:
                values = [value for value in values if value > after]
            return tuple(values[:max_items] if max_items is not None else values)

    store = PagedStore(tmp_path / "formal")
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    _receipt(snapshot, "paged-job")
    referenced_records: list[dict[str, object]] = []
    for index in range(8):
        data = f"referenced-{index}".encode()
        path = f"pdf/{hashlib.sha256(data).hexdigest()}.pdf"
        store.put(path, data)
        referenced_records.append({"path": path, "digest": hashlib.sha256(data).hexdigest(), "size": len(data)})
    orphan = b"paged orphan"
    orphan_path = f"pdf/{hashlib.sha256(orphan).hexdigest()}.pdf"
    store.put(orphan_path, orphan)
    manifest = {
        "version": 1,
        "job_id": "paged-job",
        "bundle_digest": "0" * 64,
        "resources": referenced_records,
    }

    cursor: str | None = None
    deleted: set[str] = set()
    for _ in range(5):
        result = collect_unreferenced_formal_resources(
            store,
            snapshot_db=snapshot,
            manifests=[manifest],
            limit=1,
            grace_seconds=0,
            cursor=cursor,
        )
        deleted.update(result.deleted)
        cursor = result.next_cursor
        if orphan_path in deleted:
            break

    assert deleted == {orphan_path}


def test_gc_cursor_advances_only_through_inspected_objects(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    paths: list[str] = []
    for index in range(4):
        data = f"cursor object {index}".encode()
        path = f"pdf/{hashlib.sha256(data).hexdigest()}.pdf"
        store.put(path, data)
        paths.append(path)
    paths.sort()

    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    result = collect_unreferenced_formal_resources(
        store, snapshot_db=snapshot, limit=1, grace_seconds=0
    )

    assert result.deleted and result.deleted[0] in paths
    assert result.next_cursor is not None
    assert result.next_cursor.startswith("v1l.")


def test_gc_clears_cursor_after_reaching_end_of_short_page(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    payload = b"referenced final page"
    relative = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"
    store.put(relative, payload)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    connection = open_snapshot_connection(snapshot)
    connection.execute(
        "INSERT INTO paper(paper_id,paper_key,paper_key_type,title,year,month,publication_date,"
        "venue,preferred_summary_template,summary_preview,pdf_content_hash,source_md_content_hash) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "paper-1",
            "paper-1",
            "bib",
            "Referenced",
            "2026",
            "01",
            "2026-01-01",
            "Journal",
            "simple",
            "summary",
            hashlib.sha256(payload).hexdigest(),
            None,
        ),
    )
    connection.commit()
    connection.close()

    first = collect_unreferenced_formal_resources(
        store, snapshot_db=snapshot, limit=1, grace_seconds=0
    )

    assert first.deleted == ()
    assert first.next_cursor is not None
    second = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        limit=1,
        grace_seconds=0,
        cursor=first.next_cursor,
    )
    assert second.deleted == ()
    assert second.next_cursor is None


def test_gc_clears_cursor_after_deleting_only_object_on_short_page(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    payload = b"only object"
    relative = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"
    store.put(relative, payload)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)

    first = collect_unreferenced_formal_resources(
        store, snapshot_db=snapshot, limit=1, grace_seconds=0
    )

    assert first.deleted == (relative,)
    assert first.next_cursor is not None
    second = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        limit=1,
        grace_seconds=0,
        cursor=first.next_cursor,
    )
    assert second.deleted == ()
    assert second.next_cursor is None


def test_gc_filters_unrelated_files_before_inspection_page_limit(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    payload = b"orphan after unrelated files"
    relative = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"
    store.put(relative, payload)
    for index in range(20):
        unrelated = store.root / "aaa" / f"unrelated-{index:02d}.txt"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        unrelated.write_text("not a publication object", encoding="utf-8")

    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    result = collect_unreferenced_formal_resources(
        store, snapshot_db=snapshot, limit=1, grace_seconds=0
    )

    assert result.deleted == (relative,)
    assert not (store.root / relative).exists()


def test_webdav_formal_listing_normalizes_unstable_order_and_applies_cursor(
    tmp_path: Path,
) -> None:
    first_data = b"first remote object"
    second_data = b"second remote object"
    first = f"pdf/{hashlib.sha256(first_data).hexdigest()}.pdf"
    second = f"pdf/{hashlib.sha256(second_data).hexdigest()}.pdf"

    class UnstableListing:
        def __init__(self) -> None:
            self.reversed = False

        def list(self, prefix: str = "", **_kwargs: object) -> tuple[str, ...]:
            if prefix == "published":
                return ("published/pdf/",)
            values = [f"published/{first}", f"published/{second}"]
            self.reversed = not self.reversed
            if self.reversed:
                values.reverse()
            return tuple(values)

    adapter = WebDavFormalStore(UnstableListing(), prefix="published")
    first_page = adapter.list_content_addressed_files(max_items=1)
    second_page = adapter.list_content_addressed_files(
        max_items=1, after=first_page[-1]
    )

    assert first_page == (min(first, second),)
    assert second_page == (max(first, second),)


def test_webdav_formal_listing_filters_unrelated_entries_before_page_limit() -> None:
    payload = b"remote candidate after unrelated entries"
    candidate = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"

    class Listing:
        def list(self, prefix: str = "", **_kwargs: object) -> tuple[str, ...]:
            if prefix == "":
                return ("aaa/", "pdf/")
            if prefix == "aaa":
                return ("aaa/notes.txt", "aaa/readme.md")
            if prefix == "pdf":
                return (candidate,)
            raise AssertionError(f"unexpected collection: {prefix}")

    adapter = WebDavFormalStore(Listing())

    assert adapter.list_content_addressed_files(
        max_items=1, content_addressed_only=True
    ) == (candidate,)


def test_webdav_formal_cursor_does_not_hide_parent_collection(
    tmp_path: Path,
) -> None:
    first_data = b"first cursor-aware object"
    second_data = b"second cursor-aware object"
    first = f"pdf/{hashlib.sha256(first_data).hexdigest()}.pdf"
    second = f"pdf/{hashlib.sha256(second_data).hexdigest()}.pdf"

    class CursorAwareListing:
        def list(
            self,
            prefix: str = "",
            *,
            max_items: int | None = None,
            after: str | None = None,
        ) -> tuple[str, ...]:
            values = ("pdf/",) if prefix == "" else tuple(sorted((first, second)))
            if after is not None:
                values = tuple(value for value in values if value.rstrip("/") > after.rstrip("/"))
            return values if max_items is None else values[:max_items]

    adapter = WebDavFormalStore(CursorAwareListing())
    first_page = adapter.list_content_addressed_files(max_items=1)
    second_page = adapter.list_content_addressed_files(
        max_items=1, after=first_page[-1]
    )

    assert first_page == (min(first, second),)
    assert second_page == (max(first, second),)


def test_mirrored_gc_keeps_independent_cursors_and_fair_progress_after_restart(
    tmp_path: Path,
) -> None:
    class OrderedStore:
        def __init__(self, files: dict[str, bytes]) -> None:
            self.files = dict(files)

        def list_content_addressed_files(
            self, *, max_items: int | None = None, after: str | None = None
        ) -> tuple[str, ...]:
            values = sorted(
                path
                for path in self.files
                if after is None or path > after
            )
            return tuple(values if max_items is None else values[:max_items])

        def read(self, relative: str) -> bytes:
            return self.files[relative]

        def delete(self, relative: str) -> None:
            self.files.pop(relative, None)

    primary_files: dict[str, bytes] = {}
    for index in range(12):
        data = f"primary orphan {index}".encode()
        path = f"pdf/{hashlib.sha256(data).hexdigest()}.pdf"
        primary_files[path] = data
    cache_data = b"cache-only orphan"
    cache_path = f"pdf/{hashlib.sha256(cache_data).hexdigest()}.pdf"
    primary = OrderedStore(primary_files)
    cache = OrderedStore({cache_path: cache_data})
    mirror = MirroredFormalStore(primary, cache)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    state = PipelineState(tmp_path / "queue.sqlite3")

    cursor: str | None = None
    for _ in range(6):
        result = collect_unreferenced_formal_resources(
            mirror,
            snapshot_db=snapshot,
            limit=1,
            grace_seconds=0,
            cursor=cursor,
        )
        cursor = result.next_cursor
        state.set_formal_gc_cursor(cursor)
        cursor = PipelineState(tmp_path / "queue.sqlite3").get_formal_gc_cursor()
        if cache_path not in cache.files:
            break

    assert cache_path not in cache.files
    assert primary.files


def test_mirrored_gc_continues_when_one_store_has_unexpected_listing_failure(
    tmp_path: Path,
) -> None:
    class BrokenStore:
        def list_content_addressed_files(
            self, *, max_items: int | None = None, after: str | None = None
        ) -> tuple[str, ...]:
            raise LookupError("primary temporarily unavailable")

    payload = b"cache survives primary failure"
    relative = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"
    cache = LocalFormalStore(tmp_path / "cache")
    cache.put(relative, payload)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)

    result = collect_unreferenced_formal_resources(
        MirroredFormalStore(BrokenStore(), cache),
        snapshot_db=snapshot,
        limit=1,
        grace_seconds=0,
    )

    assert result.deleted == (relative,)
    assert result.warning is not None
    assert not (cache.root / relative).exists()


def test_webdav_page_bounds_traversal_and_eventually_reaches_deep_orphan(
    tmp_path: Path,
) -> None:
    payload = b"deep bounded orphan"
    orphan = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"

    class BoundedListing:
        def __init__(self) -> None:
            self.collections: dict[str, tuple[str, ...]] = {"": tuple(
                [*(f"branch-{index}/" for index in range(5)), "pdf/"]
            )}
            for index in range(4):
                branch = f"branch-{index}"
                self.collections[branch] = tuple(
                    f"{branch}/note-{note}.txt" for note in range(3)
                )
            self.collections["branch-4"] = ("branch-4/nested/",)
            self.collections["branch-4/nested"] = ("branch-4/nested/readme.txt",)
            self.collections["pdf"] = (orphan,)
            self.calls: list[tuple[str, int | None, str | None]] = []

        def list(
            self,
            prefix: str = "",
            *,
            max_items: int | None = None,
            after: str | None = None,
        ) -> tuple[str, ...]:
            self.calls.append((prefix, max_items, after))
            assert max_items is not None
            values = self.collections.get(prefix, ())
            return tuple(
                value
                for value in values
                if after is None or value.rstrip("/") > after.rstrip("/")
            )[:max_items]

        def download(self, path: str) -> bytes:
            assert path == orphan
            return payload

        def delete(self, path: str) -> None:
            assert path == orphan
            self.collections["pdf"] = ()

    remote = BoundedListing()
    store = WebDavFormalStore(remote)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)

    cursor: str | None = None
    deleted: set[str] = set()
    for _ in range(20):
        before = len(remote.calls)
        result = collect_unreferenced_formal_resources(
            store,
            snapshot_db=snapshot,
            limit=1,
            grace_seconds=0,
            cursor=cursor,
        )
        cycle_calls = remote.calls[before:]
        assert result.inspected <= 4
        assert len(cycle_calls) <= 4
        assert all(max_items is not None for _, max_items, _ in cycle_calls)
        deleted.update(result.deleted)
        cursor = result.next_cursor
        if orphan in deleted:
            break

    assert deleted == {orphan}


def test_mirrored_gc_keeps_total_inspection_budget_across_stores(
    tmp_path: Path,
) -> None:
    referenced_data = b"referenced primary"
    referenced = f"pdf/{hashlib.sha256(referenced_data).hexdigest()}.pdf"
    cache_data = b"cache orphan"
    orphan = f"pdf/{hashlib.sha256(cache_data).hexdigest()}.pdf"

    class PagedStore:
        def __init__(self, files: dict[str, bytes]) -> None:
            self.files = dict(files)
            self.inspected = 0

        def list_content_addressed_page(
            self,
            *,
            max_items: int | None = None,
            after: str | None = None,
            content_addressed_only: bool = False,
            inspection_limit: int | None = None,
        ) -> FormalStorePage:
            del content_addressed_only
            budget = inspection_limit or 0
            values = sorted(path for path in self.files if after is None or path > after)
            inspected = min(len(values), budget)
            self.inspected += inspected
            candidates = values[:inspected]
            if max_items is not None:
                candidates = candidates[:max_items]
            next_cursor = values[inspected - 1] if inspected else after
            if inspected < len(values) and candidates:
                next_cursor = candidates[-1]
            return FormalStorePage(tuple(candidates), next_cursor, inspected)

        def read(self, relative: str) -> bytes:
            return self.files[relative]

        def delete(self, relative: str) -> None:
            self.files.pop(relative, None)

    primary = PagedStore({referenced: referenced_data})
    cache = PagedStore({orphan: cache_data})
    mirror = MirroredFormalStore(primary, cache)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    _receipt(snapshot, "job")
    manifest = {
        "version": 1,
        "job_id": "job",
        "bundle_digest": "0" * 64,
        "resources": [
            {"path": referenced, "digest": hashlib.sha256(referenced_data).hexdigest(), "size": len(referenced_data)}
        ],
    }

    result = collect_unreferenced_formal_resources(
        mirror,
        snapshot_db=snapshot,
        manifests=[manifest],
        limit=1,
        grace_seconds=0,
    )

    assert result.deleted == (orphan,)
    assert result.inspected <= 4
    assert primary.inspected + cache.inspected == result.inspected


def test_failed_pre_snapshot_manifest_does_not_pin_formal_orphan(
    tmp_path: Path,
) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "publisher")
    assert lease is not None
    state.record_publication_manifest(
        job_id,
        {
            "version": 1,
            "job_id": job_id,
            "bundle_digest": "0" * 64,
            "resources": [
                {"path": "pdf/" + "1" * 64 + ".pdf", "digest": "1" * 64, "size": 1}
            ],
        },
        lease.token,
    )
    state.transition(job_id, "failed", lease.token)
    assert state.list_publication_manifests() == [
        {
            "version": 1,
            "job_id": job_id,
            "bundle_digest": "0" * 64,
            "resources": [
                {"path": "pdf/" + "1" * 64 + ".pdf", "digest": "1" * 64, "size": 1}
            ],
        }
    ]

    payload = b"pre-snapshot orphan"
    relative = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"
    store = LocalFormalStore(tmp_path / "formal")
    store.put(relative, payload)
    snapshot = tmp_path / "snapshot.sqlite3"
    _empty_snapshot(snapshot)
    result = collect_unreferenced_formal_resources(
        store,
        snapshot_db=snapshot,
        manifests=state.list_publication_manifests(),
        limit=1,
        grace_seconds=0,
    )
    assert result.deleted == (relative,)
