"""Deterministic BibTeX matching and manual binding seams."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Callable, Iterable, Mapping

from .state import BatchMatchConflict, LeaseError, PipelineState


@dataclass(frozen=True)
class MatchResult:
    matches: list[dict[str, Any]]
    needs_attention: list[dict[str, Any]]
    unmatched_entries: list[str]


def complete_batch(state: PipelineState, job_id: str, lease_token: str | None) -> tuple[str, str]:
    """Resolve and durably bind one job from a complete batch snapshot."""
    job = state.get_job(job_id)
    batch_id = job.get("batch_id")
    if not batch_id:
        return "review_ready", "not_provided"
    batch_key = str(batch_id)
    for _ in range(8):
        snapshot = state.get_batch_matching_snapshot(batch_key)
        if not snapshot["entries"]:
            return "review_ready", "not_provided"
        explicit_binding = next(
            (binding for binding in snapshot["bindings"] if str(binding["job_id"]) == job_id),
            None,
        )
        if explicit_binding is not None and explicit_binding["entry_key"] is None:
            return "review_ready", "not_provided"
        result = snapshot["result"]
        revision = int(snapshot["revision"])
        if result is None:
            if lease_token is None or not snapshot["ready"]:
                raise LeaseError("batch summaries are not complete")
            entries = snapshot["entries"]
            entry_keys = {str(entry["key"]) for entry in entries}
            bindings = snapshot["bindings"]
            locked: dict[str, str] = {}
            bound_without_entry: set[str] = set()
            binding_attention: list[dict[str, Any]] = []
            for binding in bindings:
                bound_job = str(binding["job_id"])
                entry_key = binding["entry_key"]
                if entry_key is None:
                    bound_without_entry.add(bound_job)
                elif str(entry_key) in entry_keys:
                    locked[bound_job] = str(entry_key)
                else:
                    binding_attention.append(
                        {"job_id": bound_job, "reason": "binding_missing", "candidate_keys": []}
                    )
            inputs = {item["job_id"]: item for item in snapshot["inputs"]}
            jobs: list[dict[str, Any]] = []
            for item in snapshot["summaries"]:
                if item["job_id"] in locked or item["job_id"] in bound_without_entry or any(
                    attention["job_id"] == item["job_id"] for attention in binding_attention
                ):
                    continue
                candidate = {"job_id": item["job_id"], **inputs.get(item["job_id"], {})}
                extracted = item.get("summary")
                if isinstance(extracted, Mapping):
                    for canonical, aliases in (
                        ("title", ("title", "paper_title")),
                        ("doi", ("doi", "paper_doi")),
                    ):
                        value = next(
                            (
                                extracted.get(alias)
                                for alias in aliases
                                if isinstance(extracted.get(alias), str) and extracted.get(alias).strip()
                            ),
                            None,
                        )
                        if isinstance(value, str):
                            candidate[canonical] = value
                jobs.append(candidate)
            matched = BibTeXMatcher(state).match_batch(
                batch_key, jobs, reserved_entry_keys=locked.values()
            )
            locked_matches = [
                {"job_id": locked_job, "entry_key": entry, "reason": "existing"}
                for locked_job, entry in locked.items()
            ]
            try:
                result = state.store_batch_match_result(
                    batch_key,
                    job_id,
                    lease_token,
                    expected_revision=revision,
                    result={
                        "matches": locked_matches + matched.matches,
                        "needs_attention": binding_attention + matched.needs_attention,
                        "unmatched_entries": matched.unmatched_entries,
                    },
                )
            except BatchMatchConflict:
                continue
        matches = [item for item in result.get("matches", []) if item.get("job_id") == job_id]
        attention = [item for item in result.get("needs_attention", []) if item.get("job_id") == job_id]
        if matches and not attention:
            if lease_token is None:
                raise LeaseError("automatic binding requires active lease")
            try:
                state.bind_worker_bibtex(
                    job_id,
                    str(matches[0]["entry_key"]),
                    lease_token,
                    expected_batch_revision=revision,
                )
            except BatchMatchConflict:
                continue
            return "review_ready", "matched"
        return "needs_attention", "needs_attention"
    raise LeaseError("batch matching changed while processing")


def normalize_doi(value: str | None) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(" .;,}")


def normalize_title(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("{", "").replace("}", "")
    text = "".join(char if char.isalnum() else " " for char in text)
    return " ".join(text.split())


def normalize_key(value: str | None) -> str:
    return normalize_title(value).replace(" ", "")


class BibTeXMatcher:
    def __init__(self, state: PipelineState):
        self.state = state

    def match_batch(
        self,
        batch_id: str,
        jobs: Iterable[dict[str, Any]],
        *,
        reserved_entry_keys: Iterable[str] = (),
    ) -> MatchResult:
        entries = self.state.list_bibtex_entries(batch_id)
        if not entries:
            return MatchResult([], [], [])
        by_doi: dict[str, list[dict[str, Any]]] = {}
        by_title: dict[str, list[dict[str, Any]]] = {}
        by_key: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            doi = normalize_doi(self._field(entry, "doi"))
            title = normalize_title(self._field(entry, "title"))
            key = normalize_key(str(entry.get("key", "")))
            if doi:
                by_doi.setdefault(doi, []).append(entry)
            if title:
                by_title.setdefault(title, []).append(entry)
            if key:
                by_key.setdefault(key, []).append(entry)

        matches: list[dict[str, Any]] = []
        needs_attention: list[dict[str, Any]] = []
        used: set[str] = {str(key) for key in reserved_entry_keys}
        for supplied in jobs:
            job_id = str(supplied["job_id"])
            candidate, reason, diagnostic = self._choose(supplied, by_doi, by_title, by_key, used)
            if candidate is None:
                details = {"job_id": job_id, "reason": reason, "candidate_keys": diagnostic}
                needs_attention.append(details)
                continue
            entry_key = str(candidate["key"])
            matches.append({"job_id": job_id, "entry_key": entry_key, "reason": reason})
            used.add(entry_key)
        unmatched_entries = [str(entry["key"]) for entry in entries if str(entry["key"]) not in used]
        return MatchResult(matches, needs_attention, unmatched_entries)

    def bind_manual(
        self, job_id: str, entry_key: str | None, *, regenerate_preview: Callable[[str], object]
    ) -> dict[str, Any]:
        binding = self.state.bind_job_bibtex(job_id, entry_key, status="review_ready")
        # Callback is intentionally one narrow public seam: final metadata preview only.
        regenerate_preview(job_id)
        return binding

    @staticmethod
    def _field(entry: dict[str, Any], name: str) -> str:
        value = entry.get(name)
        if value is None and isinstance(entry.get("fields"), dict):
            value = entry["fields"].get(name)
        return str(value or "")

    @staticmethod
    def _choose(
        supplied: dict[str, Any],
        by_doi: dict[str, list[dict[str, Any]]],
        by_title: dict[str, list[dict[str, Any]]],
        by_key: dict[str, list[dict[str, Any]]],
        used: set[str],
    ) -> tuple[dict[str, Any] | None, str, list[str]]:
        doi = normalize_doi(supplied.get("doi"))
        if doi and doi in by_doi:
            candidates = [item for item in by_doi[doi] if str(item["key"]) not in used]
            if candidates:
                if len(candidates) == 1:
                    return candidates[0], "doi", []
                return None, "ambiguous_doi", [str(item["key"]) for item in candidates]
        title = normalize_title(supplied.get("title"))
        if title and title in by_title:
            candidates = [item for item in by_title[title] if str(item["key"]) not in used]
            if candidates:
                if len(candidates) == 1:
                    return candidates[0], "title", []
                return None, "ambiguous_title", [str(item["key"]) for item in candidates]
        filename = str(supplied.get("filename", ""))
        stem = PurePath(filename).stem
        key = normalize_key(stem)
        if key and key in by_key:
            candidates = [item for item in by_key[key] if str(item["key"]) not in used]
            if candidates:
                if len(candidates) == 1:
                    return candidates[0], "filename_stem", []
                return None, "ambiguous_filename_stem", [str(item["key"]) for item in candidates]
        return None, "unmatched", []


BibtexMatcher = BibTeXMatcher
