"""Deterministic BibTeX matching and manual binding seams."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Callable, Iterable

from .state import PipelineState


@dataclass(frozen=True)
class MatchResult:
    matches: list[dict[str, Any]]
    needs_attention: list[dict[str, Any]]
    unmatched_entries: list[str]


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

    def match_batch(self, batch_id: str, jobs: Iterable[dict[str, Any]]) -> MatchResult:
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
        used: set[str] = set()
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
