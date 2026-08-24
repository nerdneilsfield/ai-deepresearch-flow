from __future__ import annotations

import hashlib
from pathlib import Path

from deepresearch_flow.pipeline.formal_gc import collect_unreferenced_formal_resources
from deepresearch_flow.pipeline.publication_store import LocalFormalStore, WebDavFormalStore
from deepresearch_flow.pipeline.state import PipelineState


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
    first = collect_unreferenced_formal_resources(
        store,
        snapshot_db=tmp_path / "missing-snapshot.sqlite3",
        manifests=[manifest],
        limit=1,
        grace_seconds=0,
    )
    assert len(first.deleted) == 1
    assert (store.root / referenced_path).exists()
    second = collect_unreferenced_formal_resources(
        store,
        snapshot_db=tmp_path / "missing-snapshot.sqlite3",
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

    result = collect_unreferenced_formal_resources(
        store,
        snapshot_db=tmp_path / "missing-snapshot.sqlite3",
        limit=1,
        grace_seconds=0,
    )
    assert result.deleted == (path,)
    assert remote.files == {}


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
    assert state.list_publication_manifests() == []

    payload = b"pre-snapshot orphan"
    relative = f"pdf/{hashlib.sha256(payload).hexdigest()}.pdf"
    store = LocalFormalStore(tmp_path / "formal")
    store.put(relative, payload)
    result = collect_unreferenced_formal_resources(
        store,
        snapshot_db=tmp_path / "snapshot.sqlite3",
        manifests=state.list_publication_manifests(),
        limit=1,
        grace_seconds=0,
    )
    assert result.deleted == (relative,)
