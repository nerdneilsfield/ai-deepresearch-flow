from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib.parse import unquote


_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_ARXIV_PREFIX_RE = re.compile(r"^(?:https?://arxiv\.org/abs/|arxiv:\s*)", re.IGNORECASE)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def canonicalize_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    value = unquote(str(raw).strip())
    if not value:
        return None
    value = _DOI_PREFIX_RE.sub("", value).strip().lower()
    value = value.rstrip()
    value = value.rstrip(".,;)")
    return value or None


def canonicalize_arxiv(raw: str | None) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    value = _ARXIV_PREFIX_RE.sub("", value).strip().lower()
    value = _ARXIV_VERSION_RE.sub("", value)
    return value or None


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_punct_symbols(text: str) -> str:
    out: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat and cat[0] in {"P", "S"}:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def normalize_meta_title(raw: str | None) -> str:
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw)).lower()
    text = _strip_punct_symbols(text)
    return _collapse_ws(text)


def normalize_meta_name(raw: str | None) -> str:
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw)).lower()
    return _collapse_ws(text)


def normalize_meta_venue(raw: str | None) -> str:
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw)).lower()
    return _collapse_ws(text)


def extract_year(value: str | None) -> str | None:
    if not value:
        return None
    match = _YEAR_RE.search(str(value))
    return match.group(0) if match else None


def normalized_authors(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [normalize_meta_name(item) for item in raw]
        return sorted([item for item in items if item])
    if isinstance(raw, str):
        parts = [normalize_meta_name(part) for part in raw.split(",")]
        return sorted([part for part in parts if part])
    value = normalize_meta_name(str(raw))
    return [value] if value else []


def meta_fingerprint_json(*, title: str, authors: list[str], year: str, venue: str) -> str:
    payload = {"title": title, "authors": authors, "year": year, "venue": venue}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def meta_hash(*, title: str, authors: list[str], year: str, venue: str) -> str:
    payload = meta_fingerprint_json(title=title, authors=authors, year=year, venue=venue)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def paper_id_for_key(paper_key: str) -> str:
    digest = hashlib.sha256(f"v1|{paper_key}".encode("utf-8", errors="ignore")).hexdigest()
    return digest[:32]


@dataclass(frozen=True)
class PaperKeyCandidate:
    key_type: str  # doi|arxiv|bib|meta
    paper_key: str
    meta_fingerprint: str | None = None

    @property
    def strength(self) -> int:
        order = {"doi": 4, "arxiv": 3, "bib": 2, "meta": 1}
        return order.get(self.key_type, 0)


def _bib_fields_lower(paper: dict[str, Any]) -> dict[str, str]:
    bib = paper.get("bibtex")
    if not isinstance(bib, dict):
        return {}
    fields = bib.get("fields")
    if not isinstance(fields, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        out[str(key).lower()] = str(value)
    return out


def _extract_doi(paper: dict[str, Any]) -> str | None:
    if isinstance(paper.get("doi"), str):
        return paper.get("doi")
    if isinstance(paper.get("paper_doi"), str):
        return paper.get("paper_doi")
    fields = _bib_fields_lower(paper)
    return fields.get("doi")


def _extract_arxiv(paper: dict[str, Any]) -> str | None:
    fields = _bib_fields_lower(paper)
    for key in ("arxiv", "arxivid", "arxiv_id", "arxiv-id"):
        if key in fields:
            return fields[key]
    archive_prefix = (fields.get("archiveprefix") or fields.get("archive_prefix") or "").strip().lower()
    if archive_prefix == "arxiv" and fields.get("eprint"):
        return fields.get("eprint")
    if isinstance(paper.get("arxiv"), str):
        return paper.get("arxiv")
    if isinstance(paper.get("arxiv_id"), str):
        return paper.get("arxiv_id")
    return None


def _extract_bib_key(paper: dict[str, Any]) -> str | None:
    bib = paper.get("bibtex")
    if not isinstance(bib, dict):
        return None
    key = bib.get("key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    return None


def build_paper_key_candidates(paper: dict[str, Any]) -> list[PaperKeyCandidate]:
    candidates: list[PaperKeyCandidate] = []

    doi = canonicalize_doi(_extract_doi(paper))
    if doi:
        candidates.append(PaperKeyCandidate(key_type="doi", paper_key=f"doi:{doi}"))

    arxiv = canonicalize_arxiv(_extract_arxiv(paper))
    if arxiv:
        candidates.append(PaperKeyCandidate(key_type="arxiv", paper_key=f"arxiv:{arxiv}"))

    bib_key = _extract_bib_key(paper)
    if bib_key:
        candidates.append(PaperKeyCandidate(key_type="bib", paper_key=f"bib:{bib_key}"))

    title = normalize_meta_title(str(paper.get("paper_title") or ""))
    authors = normalized_authors(paper.get("paper_authors"))
    year = (
        extract_year(str(_bib_fields_lower(paper).get("year") or "")) or extract_year(str(paper.get("publication_date") or "")) or "unknown"
    )
    venue_raw = _bib_fields_lower(paper).get("journal") or _bib_fields_lower(paper).get("booktitle") or str(paper.get("publication_venue") or "")
    venue = normalize_meta_venue(venue_raw)
    fingerprint = meta_fingerprint_json(title=title, authors=authors, year=year, venue=venue)
    candidates.append(
        PaperKeyCandidate(
            key_type="meta",
            paper_key=f"meta:{meta_hash(title=title, authors=authors, year=year, venue=venue)}",
            meta_fingerprint=fingerprint,
        )
    )

    return candidates


def choose_preferred_key(candidates: list[PaperKeyCandidate]) -> PaperKeyCandidate:
    if not candidates:
        raise ValueError("At least one candidate key is required")
    return max(candidates, key=lambda item: item.strength)


def meta_fingerprint_divergent(
    previous_fingerprint: str | None,
    current_fingerprint: str | None,
    *,
    min_title_similarity: float,
    min_author_jaccard: float,
) -> bool:
    if not previous_fingerprint or not current_fingerprint:
        return False
    try:
        prev = json.loads(previous_fingerprint)
        cur = json.loads(current_fingerprint)
    except Exception:
        return True
    prev_title = str(prev.get("title") or "")
    cur_title = str(cur.get("title") or "")
    title_similarity = difflib.SequenceMatcher(a=prev_title, b=cur_title).ratio()

    prev_authors = {str(item) for item in (prev.get("authors") or []) if str(item)}
    cur_authors = {str(item) for item in (cur.get("authors") or []) if str(item)}
    union = prev_authors | cur_authors
    jaccard = (len(prev_authors & cur_authors) / len(union)) if union else 1.0

    return title_similarity < min_title_similarity and jaccard < min_author_jaccard

