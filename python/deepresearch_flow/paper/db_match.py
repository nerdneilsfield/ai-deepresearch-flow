from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
from typing import Any
import re
import unicodedata

from deepresearch_flow.paper.utils import stable_hash

try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except Exception:
    PYPDF_AVAILABLE = False

@dataclass(frozen=True)
class PaperIndex:
    papers: list[dict[str, Any]]
    id_by_hash: dict[str, int]
    ordered_ids: list[int]
    by_tag: dict[str, set[int]]
    by_author: dict[str, set[int]]
    by_year: dict[str, set[int]]
    by_month: dict[str, set[int]]
    by_venue: dict[str, set[int]]
    stats: dict[str, Any]
    md_path_by_hash: dict[str, Path]
    translated_md_by_hash: dict[str, dict[str, Path]]
    pdf_path_by_hash: dict[str, Path]
    template_tags: list[str]


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _parse_year_month(date_str: str | None) -> tuple[str | None, str | None]:
    if not date_str:
        return None, None
    text = str(date_str).strip()
    year = None
    month = None

    year_match = re.search(r"(19|20)\d{2}", text)
    if year_match:
        year = year_match.group(0)

    numeric_match = re.search(r"(19|20)\d{2}[-/](\d{1,2})", text)
    if numeric_match:
        m = int(numeric_match.group(2))
        if 1 <= m <= 12:
            month = f"{m:02d}"
        return year, month

    month_word = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
        r"january|february|march|april|june|july|august|september|october|november|december)",
        text.lower(),
    )
    if month_word:
        lookup = {
            "january": "01",
            "february": "02",
            "march": "03",
            "april": "04",
            "may": "05",
            "june": "06",
            "july": "07",
            "august": "08",
            "september": "09",
            "october": "10",
            "november": "11",
            "december": "12",
            "jan": "01",
            "feb": "02",
            "mar": "03",
            "apr": "04",
            "jun": "06",
            "jul": "07",
            "aug": "08",
            "sep": "09",
            "sept": "09",
            "oct": "10",
            "nov": "11",
            "dec": "12",
        }
        month = lookup.get(month_word.group(0))

    return year, month


def _normalize_month_token(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        num = int(raw)
        if 1 <= num <= 12:
            return f"{num:02d}"
    lookup = {
        "january": "01",
        "february": "02",
        "march": "03",
        "april": "04",
        "may": "05",
        "june": "06",
        "july": "07",
        "august": "08",
        "september": "09",
        "october": "10",
        "november": "11",
        "december": "12",
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "sept": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    return lookup.get(raw)


def _extract_authors(paper: dict[str, Any]) -> list[str]:
    value = paper.get("paper_authors")
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value)]


def _extract_tags(paper: dict[str, Any]) -> list[str]:
    tags = paper.get("ai_generated_tags") or []
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    return []


def _extract_keywords(paper: dict[str, Any]) -> list[str]:
    keywords = paper.get("keywords") or []
    if isinstance(keywords, list):
        return [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    if isinstance(keywords, str):
        parts = re.split(r"[;,]", keywords)
        return [part.strip() for part in parts if part.strip()]
    return []


_SUMMARY_FIELDS = (
    "summary",
    "abstract",
    "keywords",
    "question1",
    "question2",
    "question3",
    "question4",
    "question5",
    "question6",
    "question7",
    "question8",
)


def _has_summary(paper: dict[str, Any], template_tags: list[str]) -> bool:
    if template_tags:
        return True
    for key in _SUMMARY_FIELDS:
        value = paper.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _extract_venue(paper: dict[str, Any]) -> str:
    if isinstance(paper.get("bibtex"), dict):
        bib = paper.get("bibtex") or {}
        fields = bib.get("fields") or {}
        bib_type = (bib.get("type") or "").lower()
        if bib_type == "article" and fields.get("journal"):
            return str(fields.get("journal"))
        if bib_type in {"inproceedings", "conference", "proceedings"} and fields.get("booktitle"):
            return str(fields.get("booktitle"))
    return str(paper.get("publication_venue") or "")


def _available_templates(paper: dict[str, Any]) -> list[str]:
    templates = paper.get("templates")
    if not isinstance(templates, dict):
        return []
    order = paper.get("template_order") or list(templates.keys())
    seen: set[str] = set()
    available: list[str] = []
    for tag in order:
        if tag in templates and tag not in seen:
            available.append(tag)
            seen.add(tag)
    for tag in templates:
        if tag not in seen:
            available.append(tag)
            seen.add(tag)
    return available


_TITLE_PREFIX_LEN = 16
_TITLE_MIN_CHARS = 24
_TITLE_MIN_TOKENS = 4
_AUTHOR_YEAR_MIN_SIMILARITY = 0.8
_LEADING_NUMERIC_MAX_LEN = 2
_SIMILARITY_START = 0.95
_SIMILARITY_STEP = 0.05
_SIMILARITY_MAX_STEPS = 10


def _normalize_title_key(title: str) -> str:
    value = unicodedata.normalize("NFKD", title)
    greek_map = {
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "ε": "epsilon",
        "ζ": "zeta",
        "η": "eta",
        "θ": "theta",
        "ι": "iota",
        "κ": "kappa",
        "λ": "lambda",
        "μ": "mu",
        "ν": "nu",
        "ξ": "xi",
        "ο": "omicron",
        "π": "pi",
        "ρ": "rho",
        "σ": "sigma",
        "τ": "tau",
        "υ": "upsilon",
        "φ": "phi",
        "χ": "chi",
        "ψ": "psi",
        "ω": "omega",
    }
    for char, name in greek_map.items():
        value = value.replace(char, f" {name} ")
    value = re.sub(
        r"\\(alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|omicron|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega)\b",
        r" \1 ",
        value,
        flags=re.IGNORECASE,
    )
    value = value.replace("{", "").replace("}", "")
    value = value.replace("_", " ")
    value = re.sub(r"([a-z])([0-9])", r"\1 \2", value, flags=re.IGNORECASE)
    value = re.sub(r"([0-9])([a-z])", r"\1 \2", value, flags=re.IGNORECASE)
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    value = re.sub(r"\s+", " ", value).strip()
    tokens = value.split()
    if not tokens:
        return ""
    merged: list[str] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if len(token) == 1 and idx + 1 < len(tokens):
            merged.append(token + tokens[idx + 1])
            idx += 2
            continue
        merged.append(token)
        idx += 1
    return " ".join(merged)


def _compact_title_key(title_key: str) -> str:
    return title_key.replace(" ", "")


def _strip_leading_numeric_tokens(title_key: str) -> str:
    tokens = title_key.split()
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.isdigit() and len(token) <= _LEADING_NUMERIC_MAX_LEN:
            idx += 1
            continue
        break
    if idx == 0:
        return title_key
    return " ".join(tokens[idx:])


def _strip_pdf_hash_suffix(name: str) -> str:
    return re.sub(r"(?i)(\.pdf)(?:-[0-9a-f\-]{8,})$", r"\1", name)


def _extract_title_from_filename(name: str) -> str:
    base = name
    lower = base.lower()
    if lower.endswith(".md"):
        base = base[:-3]
        lower = base.lower()
    if ".pdf-" in lower:
        base = _strip_pdf_hash_suffix(base)
        lower = base.lower()
    if lower.endswith(".pdf"):
        base = base[:-4]
    base = base.replace("_", " ").strip()
    match = re.match(r"\s*\d{4}\s*-\s*(.+)$", base)
    if match:
        return match.group(1).strip()
    match = re.match(r"\s*.+?\s*-\s*\d{4}\s*-\s*(.+)$", base)
    if match:
        return match.group(1).strip()
    return base.strip()


def _clean_pdf_metadata_title(value: str | None, path: Path) -> str | None:
    if not value:
        return None
    text = str(value).replace("\x00", "").strip()
    if not text:
        return None
    text = re.sub(r"(?i)^microsoft\\s+word\\s*-\\s*", "", text)
    text = re.sub(r"(?i)^pdf\\s*-\\s*", "", text)
    text = re.sub(r"(?i)^untitled\\b", "", text).strip()
    if text.lower().endswith(".pdf"):
        text = text[:-4].strip()
    if len(text) < 3:
        return None
    stem = path.stem.strip()
    if stem and text.lower() == stem.lower():
        return None
    return text


def _read_pdf_metadata_title(path: Path) -> str | None:
    if not PYPDF_AVAILABLE:
        return None
    try:
        reader = PdfReader(str(path))
        meta = reader.metadata
        title = meta.title if meta else None
    except Exception:
        return None
    return _clean_pdf_metadata_title(title, path)


def _is_pdf_like(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return True
    name_lower = path.name.lower()
    return ".pdf-" in name_lower and not name_lower.endswith(".md")


def _scan_pdf_roots(roots: list[Path]) -> tuple[list[Path], list[dict[str, Any]]]:
    pdf_paths: list[Path] = []
    meta: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        files: list[Path] = []
        for path in root.rglob("*"):
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            if not _is_pdf_like(path):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(resolved)
        max_mtime = 0.0
        total_size = 0
        for path in files:
            try:
                stats = path.stat()
            except OSError:
                continue
            max_mtime = max(max_mtime, stats.st_mtime)
            total_size += stats.st_size
        pdf_paths.extend(files)
        meta.append(
            {
                "path": str(root),
                "count": len(files),
                "max_mtime": max_mtime,
                "size": total_size,
            }
        )
    return pdf_paths, meta


def _extract_year_author_from_filename(name: str) -> tuple[str | None, str | None]:
    base = name
    lower = base.lower()
    if lower.endswith(".md"):
        base = base[:-3]
        lower = base.lower()
    if ".pdf-" in lower:
        base = _strip_pdf_hash_suffix(base)
        lower = base.lower()
    if lower.endswith(".pdf"):
        base = base[:-4]
    match = re.match(r"\s*(.+?)\s*-\s*((?:19|20)\d{2})\s*-\s*", base)
    if match:
        return match.group(2), match.group(1).strip()
    match = re.match(r"\s*((?:19|20)\d{2})\s*-\s*", base)
    if match:
        return match.group(1), None
    return None, None


def _normalize_author_key(name: str) -> str:
    raw = name.lower().strip()
    raw = raw.replace("et al.", "").replace("et al", "")
    if "," in raw:
        raw = raw.split(",", 1)[0]
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""
    parts = raw.split()
    return parts[-1] if parts else raw


def _title_prefix_key(title_key: str) -> str | None:
    if len(title_key.split()) < _TITLE_MIN_TOKENS:
        return None
    compact = _compact_title_key(title_key)
    if len(compact) < _TITLE_PREFIX_LEN:
        return None
    prefix = compact[:_TITLE_PREFIX_LEN]
    if not prefix:
        return None
    return f"prefix:{prefix}"


def _title_overlap_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    token_count = len(shorter.split())
    if len(shorter) >= _TITLE_MIN_CHARS or token_count >= _TITLE_MIN_TOKENS:
        if longer.startswith(shorter) or shorter in longer:
            return True
    return False


def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _adaptive_similarity_match(title_key: str, candidates: list[Path]) -> tuple[Path | None, float]:
    if not title_key:
        return None, 0.0
    scored: list[tuple[Path, float]] = []
    for path in candidates:
        candidate_title = _normalize_title_key(_extract_title_from_filename(path.name))
        if not candidate_title:
            continue
        if _title_overlap_match(title_key, candidate_title):
            return path, 1.0
        scored.append((path, _title_similarity(title_key, candidate_title)))
    if not scored:
        return None, 0.0

    def matches_at(threshold: float) -> list[tuple[Path, float]]:
        return [(path, score) for path, score in scored if score >= threshold]

    threshold = _SIMILARITY_START
    step = _SIMILARITY_STEP
    prev_threshold = None
    prev_count = None
    for _ in range(_SIMILARITY_MAX_STEPS):
        matches = matches_at(threshold)
        if len(matches) == 1:
            path, score = matches[0]
            return path, score
        if len(matches) == 0:
            prev_threshold = threshold
            prev_count = 0
            threshold -= step
            continue
        if prev_count == 0 and prev_threshold is not None:
            low = threshold
            high = prev_threshold
            for _ in range(_SIMILARITY_MAX_STEPS):
                mid = (low + high) / 2
                mid_matches = matches_at(mid)
                if len(mid_matches) == 1:
                    path, score = mid_matches[0]
                    return path, score
                if len(mid_matches) == 0:
                    high = mid
                else:
                    low = mid
            return None, 0.0
        prev_threshold = threshold
        prev_count = len(matches)
        threshold -= step
    return None, 0.0


def _resolve_by_title_and_meta(
    paper: dict[str, Any],
    file_index: dict[str, list[Path]],
) -> tuple[Path | None, str | None, float]:
    title = str(paper.get("paper_title") or "")
    title_key = _normalize_title_key(title)
    if not title_key:
        title_key = ""
    candidates = file_index.get(title_key, [])
    if candidates:
        return candidates[0], "title", 1.0
    if title_key:
        compact_key = _compact_title_key(title_key)
        compact_candidates = file_index.get(f"compact:{compact_key}", [])
        if compact_candidates:
            return compact_candidates[0], "title_compact", 1.0
        stripped_key = _strip_leading_numeric_tokens(title_key)
        if stripped_key and stripped_key != title_key:
            stripped_candidates = file_index.get(stripped_key, [])
            if stripped_candidates:
                return stripped_candidates[0], "title_stripped", 1.0
            stripped_compact = _compact_title_key(stripped_key)
            stripped_candidates = file_index.get(f"compact:{stripped_compact}", [])
            if stripped_candidates:
                return stripped_candidates[0], "title_compact", 1.0
    prefix_candidates: list[Path] = []
    prefix_key = _title_prefix_key(title_key)
    if prefix_key:
        prefix_candidates = file_index.get(prefix_key, [])
    if not prefix_candidates:
        stripped_key = _strip_leading_numeric_tokens(title_key)
        if stripped_key and stripped_key != title_key:
            prefix_key = _title_prefix_key(stripped_key)
            if prefix_key:
                prefix_candidates = file_index.get(prefix_key, [])
    if prefix_candidates:
        match, score = _adaptive_similarity_match(title_key, prefix_candidates)
        if match is not None:
            match_type = "title_prefix" if score >= 1.0 else "title_fuzzy"
            return match, match_type, score
    year = str(paper.get("_year") or "").strip()
    if not year.isdigit():
        return None, None, 0.0
    author_key = ""
    authors = paper.get("_authors") or []
    if authors:
        author_key = _normalize_author_key(str(authors[0]))
    candidates = []
    match_type = "year"
    if author_key:
        candidates = file_index.get(f"authoryear:{year}:{author_key}", [])
        if candidates:
            match_type = "author_year"
    if not candidates:
        candidates = file_index.get(f"year:{year}", [])
    if not candidates:
        return None, None, 0.0
    if len(candidates) == 1 and not title_key:
        return candidates[0], match_type, 1.0
    match, score = _adaptive_similarity_match(title_key, candidates)
    if match is not None:
        if score < _AUTHOR_YEAR_MIN_SIMILARITY:
            return None, None, 0.0
        return match, "title_fuzzy", score
    return None, None, 0.0


def _build_file_index(roots: list[Path], *, suffixes: set[str]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        for path in root.rglob("*"):
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            suffix = path.suffix.lower()
            if suffix not in suffixes:
                name_lower = path.name.lower()
                if suffixes == {".pdf"} and ".pdf-" in name_lower and suffix != ".md":
                    pass
                else:
                    continue
            resolved = path.resolve()
            name_key = path.name.lower()
            index.setdefault(name_key, []).append(resolved)
            title_candidate = _extract_title_from_filename(path.name)
            title_key = _normalize_title_key(title_candidate)
            if title_key:
                if title_key != name_key:
                    index.setdefault(title_key, []).append(resolved)
                compact_key = _compact_title_key(title_key)
                if compact_key:
                    index.setdefault(f"compact:{compact_key}", []).append(resolved)
                prefix_key = _title_prefix_key(title_key)
                if prefix_key:
                    index.setdefault(prefix_key, []).append(resolved)
                stripped_key = _strip_leading_numeric_tokens(title_key)
                if stripped_key and stripped_key != title_key:
                    index.setdefault(stripped_key, []).append(resolved)
                    stripped_compact = _compact_title_key(stripped_key)
                    if stripped_compact:
                        index.setdefault(f"compact:{stripped_compact}", []).append(resolved)
                    stripped_prefix = _title_prefix_key(stripped_key)
                    if stripped_prefix:
                        index.setdefault(stripped_prefix, []).append(resolved)
            year_hint, author_hint = _extract_year_author_from_filename(path.name)
            if year_hint:
                index.setdefault(f"year:{year_hint}", []).append(resolved)
                if author_hint:
                    author_key = _normalize_author_key(author_hint)
                    if author_key:
                        index.setdefault(f"authoryear:{year_hint}:{author_key}", []).append(resolved)
    return index


def _build_file_index_from_paths(paths: list[Path], *, suffixes: set[str]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in paths:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        suffix = path.suffix.lower()
        if suffix not in suffixes:
            name_lower = path.name.lower()
            if suffixes == {".pdf"} and ".pdf-" in name_lower and suffix != ".md":
                pass
            else:
                continue
        resolved = path.resolve()
        name_key = path.name.lower()
        index.setdefault(name_key, []).append(resolved)
        title_candidate = _extract_title_from_filename(path.name)
        title_key = _normalize_title_key(title_candidate)
        if title_key:
            if title_key != name_key:
                index.setdefault(title_key, []).append(resolved)
            compact_key = _compact_title_key(title_key)
            if compact_key:
                index.setdefault(f"compact:{compact_key}", []).append(resolved)
            prefix_key = _title_prefix_key(title_key)
            if prefix_key:
                index.setdefault(prefix_key, []).append(resolved)
            stripped_key = _strip_leading_numeric_tokens(title_key)
            if stripped_key and stripped_key != title_key:
                index.setdefault(stripped_key, []).append(resolved)
                stripped_compact = _compact_title_key(stripped_key)
                if stripped_compact:
                    index.setdefault(f"compact:{stripped_compact}", []).append(resolved)
                stripped_prefix = _title_prefix_key(stripped_key)
                if stripped_prefix:
                    index.setdefault(stripped_prefix, []).append(resolved)
    return index


def _build_translated_index(roots: list[Path]) -> dict[str, dict[str, Path]]:
    index: dict[str, dict[str, Path]] = {}
    candidates: list[Path] = []
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        try:
            candidates.extend(root.rglob("*.md"))
        except OSError:
            continue
    for path in sorted(candidates, key=lambda item: str(item)):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        name = path.name
        match = re.match(r"^(.+)\.([^.]+)\.md$", name, flags=re.IGNORECASE)
        if not match:
            continue
        base_name = match.group(1).strip()
        lang = match.group(2).strip()
        if not base_name or not lang:
            continue
        base_key = base_name.lower()
        lang_key = lang.lower()
        index.setdefault(base_key, {}).setdefault(lang_key, path.resolve())
    return index


def _resolve_source_md(paper: dict[str, Any], md_index: dict[str, list[Path]]) -> Path | None:
    source_path = paper.get("source_path")
    if not source_path:
        source_path = ""
    if source_path:
        name = Path(str(source_path)).name.lower()
        candidates = md_index.get(name, [])
        if candidates:
            return candidates[0]
    match, _, _ = _resolve_by_title_and_meta(paper, md_index)
    return match


def _guess_pdf_names(paper: dict[str, Any]) -> list[str]:
    source_path = paper.get("source_path")
    if not source_path:
        return []
    name = Path(str(source_path)).name
    match = re.match(r"(?i)(.+\.pdf)(?:-[0-9a-f\-]{8,})?\.md$", name)
    if match:
        return [Path(match.group(1)).name]
    if ".pdf-" in name.lower():
        base = name[: name.lower().rfind(".pdf-") + 4]
        return [Path(base).name]
    if name.lower().endswith(".pdf"):
        return [name]
    if name.lower().endswith(".pdf.md"):
        return [name[:-3]]
    return []


def _resolve_pdf(paper: dict[str, Any], pdf_index: dict[str, list[Path]]) -> Path | None:
    for filename in _guess_pdf_names(paper):
        candidates = pdf_index.get(filename.lower(), [])
        if candidates:
            return candidates[0]
    match, _, _ = _resolve_by_title_and_meta(paper, pdf_index)
    return match


def build_index(
    papers: list[dict[str, Any]],
    *,
    md_roots: list[Path] | None = None,
    md_translated_roots: list[Path] | None = None,
    pdf_roots: list[Path] | None = None,
) -> PaperIndex:
    id_by_hash: dict[str, int] = {}
    by_tag: dict[str, set[int]] = {}
    by_author: dict[str, set[int]] = {}
    by_year: dict[str, set[int]] = {}
    by_month: dict[str, set[int]] = {}
    by_venue: dict[str, set[int]] = {}

    md_path_by_hash: dict[str, Path] = {}
    translated_md_by_hash: dict[str, dict[str, Path]] = {}
    pdf_path_by_hash: dict[str, Path] = {}

    md_file_index = _build_file_index(md_roots or [], suffixes={".md"})
    translated_index = _build_translated_index(md_translated_roots or [])
    pdf_file_index = _build_file_index(pdf_roots or [], suffixes={".pdf"})

    year_counts: dict[str, int] = {}
    month_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    keyword_counts: dict[str, int] = {}
    author_counts: dict[str, int] = {}
    venue_counts: dict[str, int] = {}
    template_tag_counts: dict[str, int] = {}

    def add_index(index: dict[str, set[int]], key: str, idx: int) -> None:
        index.setdefault(key, set()).add(idx)

    for idx, paper in enumerate(papers):
        is_pdf_only = bool(paper.get("_is_pdf_only"))
        source_hash = paper.get("source_hash")
        if not source_hash and paper.get("source_path"):
            source_hash = stable_hash(str(paper.get("source_path")))
        if source_hash:
            id_by_hash[str(source_hash)] = idx

        title = str(paper.get("paper_title") or "")
        paper["_title_lc"] = title.lower()

        bib_fields: dict[str, Any] = {}
        if isinstance(paper.get("bibtex"), dict):
            bib_fields = paper.get("bibtex", {}).get("fields", {}) or {}

        year = None
        if bib_fields.get("year") and str(bib_fields.get("year")).isdigit():
            year = str(bib_fields.get("year"))
        month = _normalize_month_token(bib_fields.get("month"))
        if not year or not month:
            parsed_year, parsed_month = _parse_year_month(str(paper.get("publication_date") or ""))
            year = year or parsed_year
            month = month or parsed_month

        year_label = year or "Unknown"
        month_label = month or "Unknown"
        paper["_year"] = year_label
        paper["_month"] = month_label
        add_index(by_year, _normalize_key(year_label), idx)
        add_index(by_month, _normalize_key(month_label), idx)
        if not is_pdf_only:
            year_counts[year_label] = year_counts.get(year_label, 0) + 1
            month_counts[month_label] = month_counts.get(month_label, 0) + 1

        venue = _extract_venue(paper).strip()
        paper["_venue"] = venue
        if venue:
            add_index(by_venue, _normalize_key(venue), idx)
            if not is_pdf_only:
                venue_counts[venue] = venue_counts.get(venue, 0) + 1
        else:
            add_index(by_venue, "unknown", idx)
            if not is_pdf_only:
                venue_counts["Unknown"] = venue_counts.get("Unknown", 0) + 1

        authors = _extract_authors(paper)
        paper["_authors"] = authors
        for author in authors:
            key = _normalize_key(author)
            add_index(by_author, key, idx)
            if not is_pdf_only:
                author_counts[author] = author_counts.get(author, 0) + 1

        tags = _extract_tags(paper)
        paper["_tags"] = tags
        for tag in tags:
            key = _normalize_key(tag)
            add_index(by_tag, key, idx)
            if not is_pdf_only:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        keywords = _extract_keywords(paper)
        paper["_keywords"] = keywords
        for keyword in keywords:
            if not is_pdf_only:
                keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1

        template_tags = _available_templates(paper)
        if not template_tags:
            fallback_tag = paper.get("template_tag") or paper.get("prompt_template")
            if fallback_tag:
                template_tags = [str(fallback_tag)]
        paper["_template_tags"] = template_tags
        paper["_template_tags_lc"] = [tag.lower() for tag in template_tags]
        paper["_has_summary"] = _has_summary(paper, template_tags)
        if not is_pdf_only:
            for tag in template_tags:
                template_tag_counts[tag] = template_tag_counts.get(tag, 0) + 1

        search_parts = [title, venue, " ".join(authors), " ".join(tags)]
        paper["_search_lc"] = " ".join(part for part in search_parts if part).lower()

        source_hash_str = str(source_hash) if source_hash else str(idx)
        md_path = _resolve_source_md(paper, md_file_index)
        if md_path is not None:
            md_path_by_hash[source_hash_str] = md_path
            base_key = md_path.with_suffix("").name.lower()
            translations = translated_index.get(base_key, {})
            if translations:
                translated_md_by_hash[source_hash_str] = translations
        pdf_path = _resolve_pdf(paper, pdf_file_index)
        if pdf_path is not None:
            pdf_path_by_hash[source_hash_str] = pdf_path

    def year_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, str]:
        idx, paper = item
        year_label = str(paper.get("_year") or "Unknown")
        title_label = str(paper.get("paper_title") or "")
        if year_label.isdigit():
            return (0, -int(year_label), title_label.lower())
        return (1, 0, title_label.lower())

    ordered_ids = [idx for idx, _ in sorted(enumerate(papers), key=year_sort_key)]

    stats_total = sum(1 for paper in papers if not paper.get("_is_pdf_only"))
    stats = {
        "total": stats_total,
        "years": _sorted_counts(year_counts, numeric_desc=True),
        "months": _sorted_month_counts(month_counts),
        "tags": _sorted_counts(tag_counts),
        "keywords": _sorted_counts(keyword_counts),
        "authors": _sorted_counts(author_counts),
        "venues": _sorted_counts(venue_counts),
    }

    template_tags = sorted(template_tag_counts.keys(), key=lambda item: item.lower())

    return PaperIndex(
        papers=papers,
        id_by_hash=id_by_hash,
        ordered_ids=ordered_ids,
        by_tag=by_tag,
        by_author=by_author,
        by_year=by_year,
        by_month=by_month,
        by_venue=by_venue,
        stats=stats,
        md_path_by_hash=md_path_by_hash,
        translated_md_by_hash=translated_md_by_hash,
        pdf_path_by_hash=pdf_path_by_hash,
        template_tags=template_tags,
    )


def _sorted_counts(counts: dict[str, int], *, numeric_desc: bool = False) -> list[dict[str, Any]]:
    items = list(counts.items())
    if numeric_desc:
        def key(item: tuple[str, int]) -> tuple[int, int]:
            label, count = item
            if label.isdigit():
                return (0, -int(label))
            return (1, 0)
        items.sort(key=key)
    else:
        items.sort(key=lambda item: item[1], reverse=True)
    return [{"label": k, "count": v} for k, v in items]


def _sorted_month_counts(counts: dict[str, int]) -> list[dict[str, Any]]:
    def month_sort(label: str) -> int:
        if label == "Unknown":
            return 99
        if label.isdigit():
            return int(label)
        return 98

    items = sorted(counts.items(), key=lambda item: month_sort(item[0]))
    return [{"label": k, "count": v} for k, v in items]
