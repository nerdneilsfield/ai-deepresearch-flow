from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from deepresearch_flow.pipeline.formal_gc import collect_unreferenced_formal_resources
from deepresearch_flow.pipeline.publication_store import LocalFormalStore, WebDavFormalStore
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


def test_webdav_formal_gc_uses_explicit_list_read_delete_capability(
    tmp_path: Path,
) -> None:
    class FakeWebDav:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        def list(self, _prefix: str = "") -> tuple[str, ...]:
            return tuple(self.files)

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

        def list(self, _prefix: str = "") -> tuple[str, ...]:
            return tuple(self.files)

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
        def list(self, prefix: str = "") -> tuple[str, ...]:
            if prefix == "published":
                return ("published/pdf/", "private/")
            if prefix == "published/pdf":
                return ("published/pdf/object.txt",)
            raise AssertionError(f"outside prefix was traversed: {prefix}")

    files = WebDavFormalStore(PrefixListing(), prefix="published").list_content_addressed_files()

    assert files == ("pdf/object.txt",)


def test_webdav_formal_store_rejects_parent_traversal_in_listing() -> None:
    class UnsafeListing:
        def list(self, _prefix: str = "") -> tuple[str, ...]:
            return ("published/../secret/",)

    with pytest.raises(ValueError, match="traversal"):
        WebDavFormalStore(UnsafeListing(), prefix="published").list_content_addressed_files()


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
