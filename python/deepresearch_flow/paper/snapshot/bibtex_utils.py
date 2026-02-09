from __future__ import annotations

import re
from typing import Any

from deepresearch_flow.paper.db_ops import bibtex_entry_to_text
from deepresearch_flow.paper.snapshot.identity import canonicalize_doi

_DOI_FROM_RAW_RE = re.compile(r"\bdoi\s*=\s*[{\"]?([^}\",\n]+)", re.IGNORECASE)


def extract_doi_from_bibtex_raw(raw: str | None) -> str | None:
    if not raw:
        return None
    match = _DOI_FROM_RAW_RE.search(str(raw))
    if not match:
        return None
    return canonicalize_doi(match.group(1))


def extract_canonical_doi(paper: dict[str, Any], bib_fields: dict[str, Any]) -> str | None:
    for key in ("doi", "paper_doi"):
        value = paper.get(key)
        if isinstance(value, str) and value.strip():
            canonical = canonicalize_doi(value)
            if canonical:
                return canonical
    bib_doi = bib_fields.get("doi")
    if bib_doi is None:
        return None
    return canonicalize_doi(str(bib_doi))


def extract_current_bibtex_payload(
    paper: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    bib = paper.get("bibtex")
    if not isinstance(bib, dict):
        return None, None, None, None

    raw_entry = bib.get("raw_entry")
    raw = str(raw_entry).strip() if isinstance(raw_entry, str) else ""
    if not raw:
        raw = bibtex_entry_to_text(bib).strip()
    if not raw:
        return None, None, None, None

    key = str(bib.get("key") or "").strip() or None
    entry_type = str(bib.get("type") or "").strip().lower() or None
    fields = bib.get("fields") if isinstance(bib.get("fields"), dict) else {}
    bib_doi = canonicalize_doi(str(fields.get("doi") or "")) if fields else None
    if not bib_doi:
        bib_doi = extract_doi_from_bibtex_raw(raw)
    return raw, key, entry_type, bib_doi
