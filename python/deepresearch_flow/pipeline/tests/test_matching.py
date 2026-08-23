from pathlib import Path

import pytest

from deepresearch_flow.pipeline.artifacts import ArtifactStore
from deepresearch_flow.pipeline.config import PipelineConfig
from deepresearch_flow.pipeline.matching import BibTeXMatcher
from deepresearch_flow.pipeline.state import BatchMatchConflict, PipelineState


def test_matches_doi_then_title_then_key_and_reports_unmatched_in_job_order(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"))
    batch = state.create_batch()
    jobs = [state.create_job(batch) for _ in range(4)]
    state.set_job_input(jobs[0], "DOI paper.pdf", "sha0", 10)
    state.set_job_input(jobs[1], "Title paper.pdf", "sha1", 10)
    state.set_job_input(jobs[2], "key-name.pdf", "sha2", 10)
    state.set_job_input(jobs[3], "missing.pdf", "sha3", 10)
    matcher = BibTeXMatcher(state)
    state.persist_bibtex_entries(batch, [
        {"key": "doi-key", "doi": "10.1000/ABC", "title": "Other"},
        {"key": "title-key", "title": "A Useful Title"},
        {"key": "key-name", "title": "Elsewhere"},
    ])

    result = matcher.match_batch(
        batch,
        [
            {"job_id": jobs[0], "doi": "10.1000/abc", "title": "Nope", "filename": "DOI paper.pdf"},
            {"job_id": jobs[1], "doi": "", "title": "A useful: title", "filename": "Title paper.pdf"},
            {"job_id": jobs[2], "doi": None, "title": "No title", "filename": "key-name.pdf"},
            {"job_id": jobs[3], "doi": None, "title": "No match", "filename": "missing.pdf"},
        ],
    )

    assert [item["entry_key"] for item in result.matches] == ["doi-key", "title-key", "key-name"]
    assert result.matches[0]["reason"] == "doi"
    assert result.matches[1]["reason"] == "title"
    assert result.matches[2]["reason"] == "filename_stem"
    assert [item["job_id"] for item in result.needs_attention] == [jobs[3]]


def test_absent_bibtex_does_not_create_attention_items(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"))
    batch = state.create_batch()
    job = state.create_job(batch)

    result = BibTeXMatcher(state).match_batch(batch, [{"job_id": job, "doi": "10.1/missing", "title": "Paper"}])

    assert result.matches == []
    assert result.needs_attention == []
    assert result.unmatched_entries == []


def test_one_bibtex_entry_can_bind_only_one_job_automatically(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"))
    batch = state.create_batch()
    first, second = state.create_job(batch), state.create_job(batch)
    state.persist_bibtex_entries(batch, [{"key": "same", "title": "One Paper"}])

    result = BibTeXMatcher(state).match_batch(
        batch,
        [
            {"job_id": first, "title": "One Paper", "filename": "first.pdf"},
            {"job_id": second, "title": "One Paper", "filename": "second.pdf"},
        ],
    )

    assert result.matches == [{"job_id": first, "entry_key": "same", "reason": "title"}]
    assert result.needs_attention == [{"job_id": second, "reason": "unmatched", "candidate_keys": []}]


def test_ambiguous_doi_title_and_key_are_needs_attention(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"))
    batch = state.create_batch()
    job = state.create_job(batch)
    state.set_job_input(job, "same.pdf", "digest", 1)
    state.persist_bibtex_entries(batch, [
        {"key": "a", "doi": "10.1/x", "title": "Same"},
        {"key": "b", "doi": "10.1/x", "title": "Same"},
    ])

    result = BibTeXMatcher(state).match_batch(
        batch, [{"job_id": job, "doi": "10.1/x", "title": "Same", "filename": "same.pdf"}]
    )

    assert result.matches == []
    assert result.needs_attention[0]["reason"] == "ambiguous_doi"
    assert result.needs_attention[0]["candidate_keys"] == ["a", "b"]


def test_manual_binding_updates_revision_and_calls_preview_seam_only(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"))
    batch = state.create_batch()
    job = state.create_job(batch)
    state.persist_bibtex_entries(batch, [{"key": "ref", "title": "Paper"}])
    lease = state.acquire_lease(job, "worker")
    assert lease is not None
    state.transition(job, "needs_attention", lease.token)
    old_revision = state.get_job(job)["revision"]
    calls: list[str] = []

    binding = BibTeXMatcher(state).bind_manual(job, "ref", regenerate_preview=calls.append)

    assert binding == {"job_id": job, "entry_key": "ref", "status": "review_ready"}
    assert state.get_job(job)["revision"] == old_revision + 1
    assert calls == [job]
    assert state.get_job_bibtex_key(job) == "ref"


def test_manual_no_bibtex_is_explicit_and_publishable(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"))
    job = state.create_job()
    lease = state.acquire_lease(job, "worker")
    assert lease is not None
    state.transition(job, "needs_attention", lease.token)
    result = BibTeXMatcher(state).bind_manual(job, None, regenerate_preview=lambda _: None)
    assert result["entry_key"] is None
    assert result["status"] == "review_ready"
    assert state.get_job_bibtex_key(job) is None


def test_manual_binding_is_restricted_to_attention_and_review_states(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"))
    batch = state.create_batch()
    state.persist_bibtex_entries(batch, [{"key": "ref", "title": "Paper"}])
    matcher = BibTeXMatcher(state)

    queued = state.create_job(batch)
    with pytest.raises(ValueError, match="manual binding"):
        matcher.bind_manual(queued, "ref", regenerate_preview=lambda _: None)

    running = state.create_job(batch)
    lease = state.acquire_lease(running, "worker")
    assert lease is not None
    with pytest.raises(ValueError, match="manual binding"):
        matcher.bind_manual(running, "ref", regenerate_preview=lambda _: None)

    rejected = state.create_job(batch)
    state.admin_transition(rejected, "rejected")
    with pytest.raises(ValueError, match="manual binding"):
        matcher.bind_manual(rejected, "ref", regenerate_preview=lambda _: None)

    review = state.create_job(batch)
    review_lease = state.acquire_lease(review, "worker")
    assert review_lease is not None
    state.transition(review, "review_ready", review_lease.token)
    assert matcher.bind_manual(review, "ref", regenerate_preview=lambda _: None)["status"] == "review_ready"


def test_stale_batch_match_snapshot_cannot_commit_after_sibling_requeue(tmp_path: Path) -> None:
    state = PipelineState(
        tmp_path / "queue.sqlite3",
        artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"),
    )
    batch = state.create_batch()
    first, second = state.create_job(batch), state.create_job(batch)
    state.persist_bibtex_entries(batch, [{"key": "first-ref", "title": "First"}])
    state.set_job_input(first, "first.pdf", "digest-1", 1, title="First")
    state.set_job_input(second, "second.pdf", "digest-2", 1, title="Second")
    first_lease = state.acquire_lease(first, "first-worker")
    second_lease = state.acquire_lease(second, "second-worker")
    assert first_lease is not None and second_lease is not None
    state.record_job_summary(first, {"paper_title": "First"}, first_lease.token)
    state.record_job_summary(second, {"paper_title": "Second"}, second_lease.token)

    snapshot = state.get_batch_matching_snapshot(batch)
    assert snapshot["ready"] is True

    state.transition(second, "failed", second_lease.token)
    state.admin_transition(second, "queued")

    with pytest.raises(BatchMatchConflict):
        state.store_batch_match_result(
            batch,
            first,
            first_lease.token,
            expected_revision=snapshot["revision"],
            result={
                "matches": [{"job_id": first, "entry_key": "first-ref", "reason": "title"}],
                "needs_attention": [],
                "unmatched_entries": [],
            },
        )
    assert state.get_batch_match_result(batch) is None


def test_batch_match_generation_retry_is_idempotent_after_requeue(tmp_path: Path) -> None:
    state = PipelineState(
        tmp_path / "queue.sqlite3",
        artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "formal"),
    )
    batch = state.create_batch()
    first, second = state.create_job(batch), state.create_job(batch)
    state.persist_bibtex_entries(batch, [{"key": "first-ref", "title": "First"}])
    for job, name in ((first, "First"), (second, "Second")):
        state.set_job_input(job, f"{name.lower()}.pdf", f"{name}-digest", 1, title=name)
    first_lease = state.acquire_lease(first, "first-worker")
    second_lease = state.acquire_lease(second, "second-worker")
    assert first_lease is not None and second_lease is not None
    state.record_job_summary(first, {"paper_title": "First"}, first_lease.token)
    state.record_job_summary(second, {"paper_title": "Second"}, second_lease.token)
    initial = state.get_batch_matching_snapshot(batch)
    result = {
        "matches": [{"job_id": first, "entry_key": "first-ref", "reason": "title"}],
        "needs_attention": [{"job_id": second, "reason": "unmatched", "candidate_keys": []}],
        "unmatched_entries": [],
    }
    state.transition(second, "failed", second_lease.token)
    state.admin_transition(second, "queued")
    with pytest.raises(BatchMatchConflict):
        state.store_batch_match_result(
            batch, first, first_lease.token, expected_revision=initial["revision"], result=result
        )

    current = state.get_batch_matching_snapshot(batch)
    stored = state.store_batch_match_result(
        batch, first, first_lease.token, expected_revision=current["revision"], result=result
    )
    repeated = state.store_batch_match_result(
        batch, first, first_lease.token, expected_revision=current["revision"], result=result
    )
    assert stored == result
    assert repeated == result
    assert state.get_batch_match_result(batch) == result
