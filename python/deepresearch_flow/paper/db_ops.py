from __future__ import annotations

from dataclasses import dataclass
import difflib
import json
from pathlib import Path
from typing import Any
import re
import unicodedata

from tqdm import tqdm

from deepresearch_flow.paper.utils import stable_hash

try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except Exception:
    PYPDF_AVAILABLE = False

try:
    from pybtex.database import parse_file
    PYBTEX_AVAILABLE = True
except Exception:
    PYBTEX_AVAILABLE = False

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
    value = re.sub(r"\$([^$]+)\$", r" \1 ", value)
    value = re.sub(r"\\[a-zA-Z]+\\*?\s*\{([^{}]*)\}", r" \1 ", value)
    value = re.sub(r"\\[a-zA-Z]+\\*?", " ", value)
    value = value.replace("^", " ")
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
        if (
            len(token) == 1
            and token.isalpha()
            and idx + 1 < len(tokens)
            and tokens[idx + 1].isalpha()
        ):
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
        if re.fullmatch(r"\d+\.\d+", token) and len(token) <= _LEADING_NUMERIC_MAX_LEN + 2:
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


def _scan_pdf_roots(
    roots: list[Path],
    *,
    show_progress: bool = False,
) -> tuple[list[Path], list[dict[str, Any]]]:
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
        iterator = root.rglob("*")
        if show_progress:
            iterator = tqdm(iterator, desc=f"scan pdf {root}", unit="file")
        for path in iterator:
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


def _build_file_index(
    roots: list[Path],
    *,
    suffixes: set[str],
    show_progress: bool = False,
) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        iterator = root.rglob("*")
        if show_progress:
            iterator = tqdm(iterator, desc=f"index {next(iter(suffixes))} {root}", unit="file")
        for path in iterator:
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


def _build_translated_index(
    roots: list[Path],
    *,
    show_progress: bool = False,
) -> dict[str, dict[str, Path]]:
    index: dict[str, dict[str, Path]] = {}
    candidates: list[Path] = []
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        try:
            iterator = root.rglob("*.md")
            if show_progress:
                iterator = tqdm(iterator, desc=f"scan translated {root}", unit="file")
            for path in iterator:
                candidates.append(path)
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


# ============================================================================
# Data Layer Helpers: Load, Merge, Cache, PDF-only Entries
# ============================================================================

_TEMPLATE_INFER_IGNORE_KEYS = {
    "source_path",
    "source_hash",
    "provider",
    "model",
    "extracted_at",
    "truncation",
    "output_language",
    "prompt_template",
}


def _load_paper_inputs(paths: list[Path]) -> list[dict[str, Any]]:
    """Load paper JSON files and infer template tags if needed."""
    # Delayed import to avoid circular dependency with template_registry
    from deepresearch_flow.paper.template_registry import (
        list_template_names_in_registry_order,
        load_schema_for_template,
    )
    
    inputs: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            raise ValueError(
                f"Input JSON must be an object with template_tag and papers (got array): {path}"
            )
        if not isinstance(payload, dict):
            raise ValueError(f"Input JSON must be an object: {path}")
        papers = payload.get("papers")
        if not isinstance(papers, list):
            raise ValueError(f"Input JSON missing papers list: {path}")
        template_tag = payload.get("template_tag")
        if not template_tag:
            template_tag = _infer_template_tag(papers, path, list_template_names_in_registry_order, load_schema_for_template)
        inputs.append({"template_tag": str(template_tag), "papers": papers})
    return inputs


def _infer_template_tag(
    papers: list[dict[str, Any]], 
    path: Path,
    list_template_names_in_registry_order,
    load_schema_for_template,
) -> str:
    """Infer template tag from paper content."""
    prompt_tags = {
        str(paper.get("prompt_template"))
        for paper in papers
        if isinstance(paper, dict) and paper.get("prompt_template")
    }
    if len(prompt_tags) == 1:
        return prompt_tags.pop()

    sample = next((paper for paper in papers if isinstance(paper, dict)), None)
    if sample is None:
        raise ValueError(f"Input JSON has no paper objects to infer template_tag: {path}")

    paper_keys = {key for key in sample.keys() if key not in _TEMPLATE_INFER_IGNORE_KEYS}
    if not paper_keys:
        raise ValueError(f"Input JSON papers have no keys to infer template_tag: {path}")

    best_tag = None
    best_score = -1
    for name in list_template_names_in_registry_order():
        schema = load_schema_for_template(name)
        schema_keys = set((schema.get("properties") or {}).keys())
        score = len(paper_keys & schema_keys)
        if score > best_score:
            best_score = score
            best_tag = name
        elif score == best_score:
            if best_tag != "simple" and name == "simple":
                best_tag = name

    if not best_tag:
        raise ValueError(f"Unable to infer template_tag from input JSON: {path}")
    return best_tag


def _build_cache_meta(
    db_paths: list[Path],
    bibtex_path: Path | None,
    pdf_roots_meta: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build cache metadata for invalidation."""
    def file_meta(path: Path) -> dict[str, Any]:
        try:
            stats = path.stat()
        except OSError as exc:
            raise ValueError(f"Failed to read input metadata for cache: {path}") from exc
        return {"path": str(path), "mtime": stats.st_mtime, "size": stats.st_size}

    meta = {
        "version": 1,
        "inputs": [file_meta(path) for path in db_paths],
        "bibtex": file_meta(bibtex_path) if bibtex_path else None,
    }
    if pdf_roots_meta is not None:
        meta["pdf_roots"] = pdf_roots_meta
    return meta


def _load_cached_papers(cache_dir: Path, meta: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Load cached papers if metadata matches."""
    meta_path = cache_dir / "db_serve_cache.meta.json"
    data_path = cache_dir / "db_serve_cache.papers.json"
    if not meta_path.exists() or not data_path.exists():
        return None
    try:
        cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if cached_meta != meta:
            return None
        cached_papers = json.loads(data_path.read_text(encoding="utf-8"))
        if not isinstance(cached_papers, list):
            return None
        return cached_papers
    except Exception:
        return None


def _write_cached_papers(cache_dir: Path, meta: dict[str, Any], papers: list[dict[str, Any]]) -> None:
    """Write cached papers and metadata."""
    meta_path = cache_dir / "db_serve_cache.meta.json"
    data_path = cache_dir / "db_serve_cache.papers.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    data_path.write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_year_for_matching(paper: dict[str, Any]) -> str | None:
    """Extract year from bibtex or publication_date for matching."""
    if isinstance(paper.get("bibtex"), dict):
        fields = paper.get("bibtex", {}).get("fields", {}) or {}
        year = fields.get("year")
        if year and str(year).isdigit():
            return str(year)
    parsed_year, _ = _parse_year_month(str(paper.get("publication_date") or ""))
    return parsed_year


def _prepare_paper_matching_fields(paper: dict[str, Any]) -> None:
    """Ensure paper has _authors and _year fields for matching."""
    if "_authors" not in paper:
        paper["_authors"] = _extract_authors(paper)
    if "_year" not in paper:
        paper["_year"] = _extract_year_for_matching(paper) or ""


def _build_pdf_only_entries(
    papers: list[dict[str, Any]],
    pdf_paths: list[Path],
    pdf_index: dict[str, list[Path]],
) -> list[dict[str, Any]]:
    """Build paper entries for unmatched PDFs."""
    matched: set[Path] = set()
    for paper in papers:
        _prepare_paper_matching_fields(paper)
        pdf_path = _resolve_pdf(paper, pdf_index)
        if pdf_path:
            matched.add(pdf_path.resolve())

    entries: list[dict[str, Any]] = []
    for path in pdf_paths:
        resolved = path.resolve()
        if resolved in matched:
            continue
        title = _read_pdf_metadata_title(resolved) or _extract_title_from_filename(resolved.name)
        if not title:
            title = resolved.stem
        year_hint, author_hint = _extract_year_author_from_filename(resolved.name)
        entry: dict[str, Any] = {
            "paper_title": title,
            "paper_authors": [author_hint] if author_hint else [],
            "publication_date": year_hint or "",
            "source_hash": stable_hash(str(resolved)),
            "source_path": str(resolved),
            "_is_pdf_only": True,
        }
        entries.append(entry)
    return entries


def _normalize_merge_title(value: str | None) -> str | None:
    """Normalize title for merging."""
    if not value:
        return None
    return str(value).replace("{", "").replace("}", "").strip().lower()


def _extract_bibtex_title(paper: dict[str, Any]) -> str | None:
    """Extract normalized title from bibtex."""
    if not isinstance(paper.get("bibtex"), dict):
        return None
    fields = paper.get("bibtex", {}).get("fields", {}) or {}
    return _normalize_merge_title(fields.get("title"))


def _extract_paper_title(paper: dict[str, Any]) -> str | None:
    """Extract normalized paper_title."""
    return _normalize_merge_title(paper.get("paper_title"))


def _titles_match(group: dict[str, Any], paper: dict[str, Any], *, threshold: float) -> bool:
    """Check if paper title matches group titles."""
    bib_title = _extract_bibtex_title(paper)
    group_bib = group.get("_merge_bibtex_titles") or set()
    if bib_title and group_bib:
        return any(_title_similarity(bib_title, existing) >= threshold for existing in group_bib)

    paper_title = _extract_paper_title(paper)
    group_titles = group.get("_merge_paper_titles") or set()
    if paper_title and group_titles:
        return any(_title_similarity(paper_title, existing) >= threshold for existing in group_titles)
    return False


def _add_merge_titles(group: dict[str, Any], paper: dict[str, Any]) -> None:
    """Add paper titles to group merge tracking."""
    bib_title = _extract_bibtex_title(paper)
    if bib_title:
        group.setdefault("_merge_bibtex_titles", set()).add(bib_title)
    paper_title = _extract_paper_title(paper)
    if paper_title:
        group.setdefault("_merge_paper_titles", set()).add(paper_title)


def _merge_paper_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge paper inputs from multiple template extractions."""
    merged: list[dict[str, Any]] = []
    threshold = 0.95
    prefix_len = 5
    source_hash_index: dict[str, int] = {}
    bibtex_exact: dict[str, set[int]] = {}
    bibtex_prefix: dict[str, set[int]] = {}
    paper_exact: dict[str, set[int]] = {}
    paper_prefix: dict[str, set[int]] = {}

    def prefix_key(value: str) -> str:
        return value[:prefix_len] if len(value) >= prefix_len else value

    def add_index(
        value: str,
        exact_index: dict[str, set[int]],
        prefix_index: dict[str, set[int]],
        idx: int,
    ) -> None:
        exact_index.setdefault(value, set()).add(idx)
        prefix_index.setdefault(prefix_key(value), set()).add(idx)

    def candidate_ids(bib_title: str | None, paper_title: str | None) -> list[int]:
        ids: set[int] = set()
        if bib_title:
            ids |= bibtex_exact.get(bib_title, set())
            ids |= bibtex_prefix.get(prefix_key(bib_title), set())
        if paper_title:
            ids |= paper_exact.get(paper_title, set())
            ids |= paper_prefix.get(prefix_key(paper_title), set())
        return sorted(ids)

    for bundle in inputs:
        template_tag = bundle.get("template_tag")
        papers = bundle.get("papers") or []
        for paper in papers:
            if not isinstance(paper, dict):
                raise ValueError("Input papers must be objects")
            source_hash = paper.get("source_hash")
            source_hash_key = str(source_hash) if source_hash else None
            bib_title = _extract_bibtex_title(paper)
            paper_title = _extract_paper_title(paper)
            match = None
            match_idx = None
            if source_hash_key and source_hash_key in source_hash_index:
                match_idx = source_hash_index[source_hash_key]
                match = merged[match_idx]
            else:
                for idx in candidate_ids(bib_title, paper_title):
                    candidate = merged[idx]
                    if _titles_match(candidate, paper, threshold=threshold):
                        match = candidate
                        match_idx = idx
                        break
            if match is None:
                group = {
                    "templates": {template_tag: paper},
                    "template_order": [template_tag],
                }
                _add_merge_titles(group, paper)
                merged.append(group)
                group_idx = len(merged) - 1
                if source_hash_key:
                    source_hash_index[source_hash_key] = group_idx
                if bib_title:
                    add_index(bib_title, bibtex_exact, bibtex_prefix, group_idx)
                if paper_title:
                    add_index(paper_title, paper_exact, paper_prefix, group_idx)
            else:
                templates = match.setdefault("templates", {})
                templates[template_tag] = paper
                order = match.setdefault("template_order", [])
                if template_tag not in order:
                    order.append(template_tag)
                _add_merge_titles(match, paper)
                if match_idx is not None:
                    if source_hash_key:
                        source_hash_index[source_hash_key] = match_idx
                    if bib_title:
                        add_index(bib_title, bibtex_exact, bibtex_prefix, match_idx)
                    if paper_title:
                        add_index(paper_title, paper_exact, paper_prefix, match_idx)

    preferred_defaults = ("simple", "simple_phi")
    for group in merged:
        templates = group.get("templates") or {}
        order = group.get("template_order") or list(templates.keys())
        default_tag = next((tag for tag in preferred_defaults if tag in order), None)
        if default_tag is None:
            default_tag = order[0] if order else None
        group["default_template"] = default_tag
        if default_tag and default_tag in templates:
            base = templates[default_tag]
            for key, value in base.items():
                group[key] = value
        group.pop("_merge_bibtex_titles", None)
        group.pop("_merge_paper_titles", None)
    return merged


def _normalize_bibtex_title(title: str) -> str:
    """Normalize bibtex title for matching."""
    value = title.replace("{", "").replace("}", "")
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def enrich_with_bibtex(papers: list[dict[str, Any]], bibtex_path: Path) -> None:
    """Enrich papers with bibtex metadata."""
    if not PYBTEX_AVAILABLE:
        raise RuntimeError("pybtex is required for --bibtex support")

    bib_data = parse_file(str(bibtex_path))
    entries: list[dict[str, Any]] = []
    by_prefix: dict[str, list[int]] = {}
    for key, entry in bib_data.entries.items():
        fields = dict(entry.fields)
        title = str(fields.get("title") or "").strip()
        title_norm = _normalize_bibtex_title(title)
        if not title_norm:
            continue
        record = {
            "key": key,
            "type": entry.type,
            "fields": fields,
            "persons": {role: [str(p) for p in persons] for role, persons in entry.persons.items()},
            "_title_norm": title_norm,
        }
        idx = len(entries)
        entries.append(record)
        prefix = title_norm[:16]
        by_prefix.setdefault(prefix, []).append(idx)

    for paper in papers:
        if isinstance(paper.get("bibtex"), dict):
            continue
        title = str(paper.get("paper_title") or "").strip()
        if not title:
            continue
        norm = _normalize_bibtex_title(title)
        if not norm:
            continue

        candidates = []
        prefix = norm[:16]
        for cand_idx in by_prefix.get(prefix, []):
            candidates.append(entries[cand_idx])
        if not candidates:
            candidates = entries

        best = None
        best_score = 0.0
        for entry in candidates:
            score = _title_similarity(norm, entry["_title_norm"])
            if score > best_score:
                best_score = score
                best = entry

        if best is not None and best_score >= 0.9:
            paper["bibtex"] = {k: v for k, v in best.items() if not k.startswith("_")}


def load_and_merge_papers(
    db_paths: list[Path],
    bibtex_path: Path | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    pdf_roots: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """Load and merge papers from multiple JSON files, with optional caching and PDF-only entries."""
    cache_meta = None
    pdf_roots = pdf_roots or []
    pdf_paths: list[Path] = []
    pdf_roots_meta: list[dict[str, Any]] | None = None
    if pdf_roots:
        pdf_paths, pdf_roots_meta = _scan_pdf_roots(pdf_roots)
    if cache_dir and use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_meta = _build_cache_meta(db_paths, bibtex_path, pdf_roots_meta)
        cached = _load_cached_papers(cache_dir, cache_meta)
        if cached is not None:
            return cached

    inputs = _load_paper_inputs(db_paths)
    if bibtex_path is not None:
        for bundle in inputs:
            enrich_with_bibtex(bundle["papers"], bibtex_path)
    papers = _merge_paper_inputs(inputs)
    if pdf_paths:
        pdf_index = _build_file_index_from_paths(pdf_paths, suffixes={".pdf"})
        papers.extend(_build_pdf_only_entries(papers, pdf_paths, pdf_index))

    if cache_dir and use_cache and cache_meta is not None:
        _write_cached_papers(cache_dir, cache_meta, papers)
    return papers


# ============================================================================
# Compare Logic for paper db compare
# ============================================================================

from typing import Literal


@dataclass
class CompareResult:
    """Result of comparing two datasets."""
    side: Literal["A", "B", "MATCH"]
    source_hash: str
    title: str
    match_status: Literal["matched", "only_in_A", "only_in_B", "matched_pair"]
    match_type: str | None = None
    match_score: float = 0.0
    source_path: str | None = None
    other_source_hash: str | None = None
    other_title: str | None = None
    other_source_path: str | None = None
    lang: str | None = None


@dataclass
class CompareDataset:
    """Prepared dataset for compare."""
    papers: list[dict[str, Any]]
    md_index: dict[str, list[Path]]
    pdf_index: dict[str, list[Path]]
    translated_index: dict[str, dict[str, Path]]
    paper_index: dict[str, list[dict[str, Any]]]
    path_to_index: dict[Path, int]
    hash_to_index: dict[str, int]
    paper_id_to_index: dict[int, int]


def _scan_md_roots(roots: list[Path], *, show_progress: bool = False) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        try:
            iterator = root.rglob("*.md")
            if show_progress:
                iterator = tqdm(iterator, desc=f"scan md {root}", unit="file")
            for path in iterator:
                try:
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                paths.append(path.resolve())
        except OSError:
            continue
    return paths


def _merge_file_indexes(*indexes: dict[str, list[Path]]) -> dict[str, list[Path]]:
    merged: dict[str, list[Path]] = {}
    for index in indexes:
        for key, paths in index.items():
            merged.setdefault(key, []).extend(paths)
    return merged


def _build_md_only_entries(
    papers: list[dict[str, Any]],
    md_paths: list[Path],
    md_index: dict[str, list[Path]],
) -> list[dict[str, Any]]:
    matched: set[Path] = set()
    for paper in papers:
        _prepare_paper_matching_fields(paper)
        md_path = _resolve_source_md(paper, md_index)
        if md_path:
            matched.add(md_path.resolve())
    entries: list[dict[str, Any]] = []
    for path in md_paths:
        resolved = path.resolve()
        if resolved in matched:
            continue
        title = _extract_title_from_filename(resolved.name) or resolved.stem
        year_hint, author_hint = _extract_year_author_from_filename(resolved.name)
        entry: dict[str, Any] = {
            "paper_title": title,
            "paper_authors": [author_hint] if author_hint else [],
            "publication_date": year_hint or "",
            "source_hash": stable_hash(str(resolved)),
            "source_path": str(resolved),
            "_is_md_only": True,
        }
        entries.append(entry)
    return entries


def _translation_base_key_for_paper(paper: dict[str, Any]) -> str:
    source_path = str(paper.get("source_path") or "")
    if source_path:
        return Path(source_path).stem.lower()
    title = str(paper.get("paper_title") or "")
    return _normalize_title_key(title)


def _build_translated_only_entries(
    papers: list[dict[str, Any]],
    translated_index: dict[str, dict[str, Path]],
    lang: str,
) -> list[dict[str, Any]]:
    if not lang:
        return []
    lang_key = lang.lower()
    matched: set[Path] = set()
    for paper in papers:
        base_key = _translation_base_key_for_paper(paper)
        if not base_key:
            continue
        path = translated_index.get(base_key, {}).get(lang_key)
        if path:
            matched.add(path.resolve())
    entries: list[dict[str, Any]] = []
    for base_key, translations in translated_index.items():
        path = translations.get(lang_key)
        if not path:
            continue
        resolved = path.resolve()
        if resolved in matched:
            continue
        title = _extract_title_from_filename(resolved.name) or resolved.stem
        year_hint, author_hint = _extract_year_author_from_filename(resolved.name)
        entry: dict[str, Any] = {
            "paper_title": title,
            "paper_authors": [author_hint] if author_hint else [],
            "publication_date": year_hint or "",
            "source_hash": stable_hash(str(resolved)),
            "source_path": str(resolved),
            "_is_translated_only": True,
            "translation_lang": lang_key,
        }
        entries.append(entry)
    return entries


def _build_paper_index(papers: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for paper in papers:
        _prepare_paper_matching_fields(paper)
        title = str(paper.get("paper_title") or "")
        title_key = _normalize_title_key(title)
        if title_key:
            index.setdefault(title_key, []).append(paper)
            compact_key = _compact_title_key(title_key)
            if compact_key:
                index.setdefault(f"compact:{compact_key}", []).append(paper)
            prefix_key = _title_prefix_key(title_key)
            if prefix_key:
                index.setdefault(prefix_key, []).append(paper)
            stripped_key = _strip_leading_numeric_tokens(title_key)
            if stripped_key and stripped_key != title_key:
                index.setdefault(stripped_key, []).append(paper)
                stripped_compact = _compact_title_key(stripped_key)
                if stripped_compact:
                    index.setdefault(f"compact:{stripped_compact}", []).append(paper)
                stripped_prefix = _title_prefix_key(stripped_key)
                if stripped_prefix:
                    index.setdefault(stripped_prefix, []).append(paper)
        year = str(paper.get("_year") or "").strip()
        if year:
            index.setdefault(f"year:{year}", []).append(paper)
            authors = paper.get("_authors") or []
            if authors:
                author_key = _normalize_author_key(str(authors[0]))
                if author_key:
                    index.setdefault(f"authoryear:{year}:{author_key}", []).append(paper)
    return index


def _adaptive_similarity_match_papers(
    title_key: str,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, float]:
    if not title_key:
        return None, 0.0
    scored: list[tuple[dict[str, Any], float]] = []
    for paper in candidates:
        candidate_title = _normalize_title_key(str(paper.get("paper_title") or ""))
        if not candidate_title:
            continue
        if _title_overlap_match(title_key, candidate_title):
            return paper, 1.0
        scored.append((paper, _title_similarity(title_key, candidate_title)))
    if not scored:
        return None, 0.0

    def matches_at(threshold: float) -> list[tuple[dict[str, Any], float]]:
        return [(paper, score) for paper, score in scored if score >= threshold]

    threshold = _SIMILARITY_START
    step = _SIMILARITY_STEP
    prev_threshold = None
    prev_count = None
    for _ in range(_SIMILARITY_MAX_STEPS):
        matches = matches_at(threshold)
        if len(matches) == 1:
            paper, score = matches[0]
            return paper, score
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
                    paper, score = mid_matches[0]
                    return paper, score
                if len(mid_matches) == 0:
                    high = mid
                else:
                    low = mid
            return None, 0.0
        prev_threshold = threshold
        prev_count = len(matches)
        threshold -= step
    return None, 0.0


def _resolve_paper_by_title_and_meta(
    paper: dict[str, Any],
    paper_index: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str | None, float]:
    title = str(paper.get("paper_title") or "")
    title_key = _normalize_title_key(title)
    if not title_key:
        title_key = ""
    candidates = paper_index.get(title_key, [])
    if candidates:
        return candidates[0], "title", 1.0
    if title_key:
        compact_key = _compact_title_key(title_key)
        compact_candidates = paper_index.get(f"compact:{compact_key}", [])
        if compact_candidates:
            return compact_candidates[0], "title_compact", 1.0
        stripped_key = _strip_leading_numeric_tokens(title_key)
        if stripped_key and stripped_key != title_key:
            stripped_candidates = paper_index.get(stripped_key, [])
            if stripped_candidates:
                return stripped_candidates[0], "title_stripped", 1.0
            stripped_compact = _compact_title_key(stripped_key)
            stripped_candidates = paper_index.get(f"compact:{stripped_compact}", [])
            if stripped_candidates:
                return stripped_candidates[0], "title_compact", 1.0
    prefix_candidates: list[dict[str, Any]] = []
    prefix_key = _title_prefix_key(title_key)
    if prefix_key:
        prefix_candidates = paper_index.get(prefix_key, [])
    if not prefix_candidates:
        stripped_key = _strip_leading_numeric_tokens(title_key)
        if stripped_key and stripped_key != title_key:
            prefix_key = _title_prefix_key(stripped_key)
            if prefix_key:
                prefix_candidates = paper_index.get(prefix_key, [])
    if prefix_candidates:
        match, score = _adaptive_similarity_match_papers(title_key, prefix_candidates)
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
        candidates = paper_index.get(f"authoryear:{year}:{author_key}", [])
        if candidates:
            match_type = "author_year"
    if not candidates:
        candidates = paper_index.get(f"year:{year}", [])
    if not candidates:
        return None, None, 0.0
    if len(candidates) == 1 and not title_key:
        return candidates[0], match_type, 1.0
    match, score = _adaptive_similarity_match_papers(title_key, candidates)
    if match is not None:
        if score < _AUTHOR_YEAR_MIN_SIMILARITY:
            return None, None, 0.0
        return match, "title_fuzzy", score
    return None, None, 0.0


def _get_paper_identifier(paper: dict[str, Any]) -> str:
    """Get a unique identifier for a paper."""
    return str(paper.get("source_hash") or paper.get("source_path", ""))


def _match_datasets_with_pairs(
    dataset_a: CompareDataset,
    dataset_b: CompareDataset,
    *,
    lang: str | None = None,
    show_progress: bool = False,
) -> tuple[list[CompareResult], list[tuple[int, int, str | None, float]]]:
    """Match papers between two datasets using db_ops parity."""
    results: list[CompareResult] = []
    matched_a: set[int] = set()
    matched_b: set[int] = set()
    matched_b_info: dict[int, tuple[int, str | None, float]] = {}
    match_pairs: list[tuple[int, int, str | None, float]] = []

    file_index_b = _merge_file_indexes(dataset_b.md_index, dataset_b.pdf_index)

    papers_a_iter = dataset_a.papers
    if show_progress:
        papers_a_iter = tqdm(dataset_a.papers, desc="match A", unit="paper")

    for idx_a, paper in enumerate(papers_a_iter):
        _prepare_paper_matching_fields(paper)
        source_hash = str(paper.get("source_hash") or "")
        title = str(paper.get("paper_title") or "")
        source_path = str(paper.get("source_path") or "")

        match_type = None
        match_score = 0.0
        match_status = "only_in_A"
        matched_b_idx: int | None = None
        matched_b_paper: dict[str, Any] | None = None

        if source_hash and source_hash in dataset_b.hash_to_index:
            matched_b_idx = dataset_b.hash_to_index[source_hash]
            matched_b_paper = dataset_b.papers[matched_b_idx]
            match_status = "matched"
            match_type = "hash"
            match_score = 1.0
        else:
            if file_index_b:
                matched_path, mt, score = _resolve_by_title_and_meta(paper, file_index_b)
                if matched_path is not None:
                    matched_b_idx = dataset_b.path_to_index.get(matched_path.resolve())
                    matched_b_paper = dataset_b.papers[matched_b_idx] if matched_b_idx is not None else None
                    match_status = "matched"
                    match_type = mt
                    match_score = score
            if matched_b_idx is None:
                match_paper, mt, score = _resolve_paper_by_title_and_meta(paper, dataset_b.paper_index)
                if match_paper is not None:
                    matched_b_idx = dataset_b.paper_id_to_index.get(id(match_paper))
                    matched_b_paper = match_paper
                    match_status = "matched"
                    match_type = mt
                    match_score = score
            if matched_b_idx is None and lang:
                base_key = _translation_base_key_for_paper(paper)
                if base_key:
                    translated_path = dataset_b.translated_index.get(base_key, {}).get(lang.lower())
                    if translated_path is not None:
                        matched_b_idx = dataset_b.path_to_index.get(translated_path.resolve())
                        matched_b_paper = dataset_b.papers[matched_b_idx] if matched_b_idx is not None else None
                        match_status = "matched"
                        match_type = f"translated_{lang.lower()}"
                        match_score = 1.0

        other_hash = None
        other_title = None
        other_path = None
        if matched_b_idx is not None and matched_b_paper is not None:
            matched_a.add(idx_a)
            matched_b.add(matched_b_idx)
            other_hash = str(matched_b_paper.get("source_hash") or "")
            other_title = str(matched_b_paper.get("paper_title") or "")
            other_path = str(matched_b_paper.get("source_path") or "")
            matched_b_info[matched_b_idx] = (idx_a, match_type, match_score)
            match_pairs.append((idx_a, matched_b_idx, match_type, match_score))

        results.append(
            CompareResult(
                side="A",
                source_hash=source_hash,
                title=title,
                match_status=match_status,
                match_type=match_type,
                match_score=match_score,
                source_path=source_path if source_path else None,
                other_source_hash=other_hash,
                other_title=other_title,
                other_source_path=other_path,
                lang=lang.lower() if lang else None,
            )
        )

    papers_b_iter = dataset_b.papers
    if show_progress:
        papers_b_iter = tqdm(dataset_b.papers, desc="match B", unit="paper")

    for idx_b, paper in enumerate(papers_b_iter):
        _prepare_paper_matching_fields(paper)
        source_hash = str(paper.get("source_hash") or "")
        title = str(paper.get("paper_title") or "")
        source_path = str(paper.get("source_path") or "")
        match_status = "only_in_B"
        match_type = None
        match_score = 0.0
        other_hash = None
        other_title = None
        other_path = None
        if idx_b in matched_b:
            match_status = "matched"
            info = matched_b_info.get(idx_b)
            if info:
                idx_a, match_type, match_score = info
                a_paper = dataset_a.papers[idx_a]
                other_hash = str(a_paper.get("source_hash") or "")
                other_title = str(a_paper.get("paper_title") or "")
                other_path = str(a_paper.get("source_path") or "")
        results.append(
            CompareResult(
                side="B",
                source_hash=source_hash,
                title=title,
                match_status=match_status,
                match_type=match_type,
                match_score=match_score,
                source_path=source_path if source_path else None,
                other_source_hash=other_hash,
                other_title=other_title,
                other_source_path=other_path,
                lang=lang.lower() if lang else None,
            )
        )

    for idx_a, idx_b, match_type, match_score in match_pairs:
        paper_a = dataset_a.papers[idx_a]
        paper_b = dataset_b.papers[idx_b]
        results.append(
            CompareResult(
                side="MATCH",
                source_hash=str(paper_a.get("source_hash") or ""),
                title=str(paper_a.get("paper_title") or ""),
                match_status="matched_pair",
                match_type=match_type,
                match_score=match_score,
                source_path=str(paper_a.get("source_path") or "") or None,
                other_source_hash=str(paper_b.get("source_hash") or ""),
                other_title=str(paper_b.get("paper_title") or ""),
                other_source_path=str(paper_b.get("source_path") or "") or None,
                lang=lang.lower() if lang else None,
            )
        )

    return results, match_pairs


def _match_datasets(
    dataset_a: CompareDataset,
    dataset_b: CompareDataset,
    *,
    lang: str | None = None,
    show_progress: bool = False,
) -> list[CompareResult]:
    results, _ = _match_datasets_with_pairs(
        dataset_a, dataset_b, lang=lang, show_progress=show_progress
    )
    return results


def build_compare_dataset(
    *,
    json_paths: list[Path] | None = None,
    pdf_roots: list[Path] | None = None,
    md_roots: list[Path] | None = None,
    md_translated_roots: list[Path] | None = None,
    bibtex_path: Path | None = None,
    lang: str | None = None,
    show_progress: bool = False,
) -> CompareDataset:
    """Load and index a dataset from various sources."""
    papers: list[dict[str, Any]] = []

    # Load from JSON files
    if json_paths:
        for path in json_paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # Array format - direct list of papers
                papers.extend(data)
            elif isinstance(data, dict):
                # Object format with template_tag and papers
                if isinstance(data.get("papers"), list):
                    papers.extend(data["papers"])
            else:
                raise ValueError(f"Invalid JSON format in {path}")

        # Enrich with bibtex if provided
        if bibtex_path and PYBTEX_AVAILABLE:
            enrich_with_bibtex(papers, bibtex_path)

    for paper in papers:
        _prepare_paper_matching_fields(paper)

    md_paths = _scan_md_roots(md_roots or [], show_progress=show_progress)
    pdf_paths, _ = _scan_pdf_roots(pdf_roots or [], show_progress=show_progress)
    md_index = _build_file_index(
        md_roots or [], suffixes={".md"}, show_progress=show_progress
    )
    pdf_index = _build_file_index(
        pdf_roots or [], suffixes={".pdf"}, show_progress=show_progress
    )
    translated_index = _build_translated_index(
        md_translated_roots or [], show_progress=show_progress
    )

    if pdf_paths:
        papers.extend(_build_pdf_only_entries(papers, pdf_paths, pdf_index))
    if md_paths:
        papers.extend(_build_md_only_entries(papers, md_paths, md_index))
    if translated_index and lang:
        papers.extend(_build_translated_only_entries(papers, translated_index, lang))

    for paper in papers:
        _prepare_paper_matching_fields(paper)

    paper_index = _build_paper_index(papers)
    path_to_index: dict[Path, int] = {}
    hash_to_index: dict[str, int] = {}
    paper_id_to_index: dict[int, int] = {}
    for idx, paper in enumerate(papers):
        paper_id_to_index[id(paper)] = idx
        source_hash = str(paper.get("source_hash") or "")
        if source_hash and source_hash not in hash_to_index:
            hash_to_index[source_hash] = idx
        source_path = paper.get("source_path")
        if source_path:
            path_to_index[Path(str(source_path)).resolve()] = idx

    return CompareDataset(
        papers=papers,
        md_index=md_index,
        pdf_index=pdf_index,
        translated_index=translated_index,
        paper_index=paper_index,
        path_to_index=path_to_index,
        hash_to_index=hash_to_index,
        paper_id_to_index=paper_id_to_index,
    )


def compare_datasets(
    *,
    json_paths_a: list[Path] | None = None,
    pdf_roots_a: list[Path] | None = None,
    md_roots_a: list[Path] | None = None,
    md_translated_roots_a: list[Path] | None = None,
    json_paths_b: list[Path] | None = None,
    pdf_roots_b: list[Path] | None = None,
    md_roots_b: list[Path] | None = None,
    md_translated_roots_b: list[Path] | None = None,
    bibtex_path: Path | None = None,
    lang: str | None = None,
    show_progress: bool = False,
) -> list[CompareResult]:
    """Compare two datasets and return comparison results."""
    results, _, _, _ = compare_datasets_with_pairs(
        json_paths_a=json_paths_a,
        pdf_roots_a=pdf_roots_a,
        md_roots_a=md_roots_a,
        md_translated_roots_a=md_translated_roots_a,
        json_paths_b=json_paths_b,
        pdf_roots_b=pdf_roots_b,
        md_roots_b=md_roots_b,
        md_translated_roots_b=md_translated_roots_b,
        bibtex_path=bibtex_path,
        lang=lang,
        show_progress=show_progress,
    )
    return results


def compare_datasets_with_pairs(
    *,
    json_paths_a: list[Path] | None = None,
    pdf_roots_a: list[Path] | None = None,
    md_roots_a: list[Path] | None = None,
    md_translated_roots_a: list[Path] | None = None,
    json_paths_b: list[Path] | None = None,
    pdf_roots_b: list[Path] | None = None,
    md_roots_b: list[Path] | None = None,
    md_translated_roots_b: list[Path] | None = None,
    bibtex_path: Path | None = None,
    lang: str | None = None,
    show_progress: bool = False,
) -> tuple[list[CompareResult], list[tuple[int, int, str | None, float]], CompareDataset, CompareDataset]:
    # Validate language requirement for translated inputs
    has_translated_a = md_translated_roots_a is not None and len(md_translated_roots_a) > 0
    has_translated_b = md_translated_roots_b is not None and len(md_translated_roots_b) > 0

    if (has_translated_a or has_translated_b) and lang is None:
        raise ValueError(
            "--lang parameter is required when comparing translated Markdown datasets"
        )

    dataset_a = build_compare_dataset(
        json_paths=json_paths_a,
        pdf_roots=pdf_roots_a,
        md_roots=md_roots_a,
        md_translated_roots=md_translated_roots_a,
        bibtex_path=bibtex_path,
        lang=lang,
        show_progress=show_progress,
    )

    dataset_b = build_compare_dataset(
        json_paths=json_paths_b,
        pdf_roots=pdf_roots_b,
        md_roots=md_roots_b,
        md_translated_roots=md_translated_roots_b,
        bibtex_path=bibtex_path,
        lang=lang,
        show_progress=show_progress,
    )

    results, match_pairs = _match_datasets_with_pairs(
        dataset_a, dataset_b, lang=lang, show_progress=show_progress
    )
    return results, match_pairs, dataset_a, dataset_b
