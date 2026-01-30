from __future__ import annotations

from dataclasses import dataclass
import base64
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid

from deepresearch_flow.paper.db_ops import build_index, load_and_merge_papers

from deepresearch_flow.paper.render import load_default_template
from deepresearch_flow.paper.template_registry import load_render_template
from deepresearch_flow.paper.snapshot.identity import (
    PaperKeyCandidate,
    build_paper_key_candidates,
    choose_preferred_key,
    meta_fingerprint_divergent,
    paper_id_for_key,
)
from deepresearch_flow.paper.snapshot.schema import (
    init_snapshot_db,
    recompute_facet_counts,
    recompute_paper_index,
)
from deepresearch_flow.paper.snapshot.text import (
    insert_cjk_spaces,
    markdown_to_plain_text,
)
from deepresearch_flow.paper.utils import stable_hash


@dataclass(frozen=True)
class SnapshotBuildOptions:
    input_paths: list[Path]
    bibtex_path: Path | None
    md_roots: list[Path]
    md_translated_roots: list[Path]
    pdf_roots: list[Path]
    output_db: Path
    static_export_dir: Path
    previous_snapshot_db: Path | None
    min_meta_title_similarity: float = 0.6
    min_meta_author_jaccard: float = 0.4


@dataclass(frozen=True)
class PreviousAlias:
    paper_id: str
    paper_key_type: str
    meta_fingerprint: str | None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_DATA_URL_PATTERN = re.compile(r"^data:([^;,]+)(;base64)?,(.*)$", re.DOTALL)
_IMG_TAG_PATTERN = re.compile(r"<img\\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_PATTERN = re.compile(r"\\bsrc\\s*=\\s*(\"[^\"]*\"|'[^']*'|[^\\s>]+)", re.IGNORECASE | re.DOTALL)
_EXTENSION_OVERRIDES = {".jpe": ".jpg"}
_WHITESPACE_RE = re.compile(r"\s+")


def _split_link_target(raw_link: str) -> tuple[str, str, str, str]:
    link = raw_link.strip()
    if link.startswith("<"):
        end = link.find(">")
        if end != -1:
            return link[1:end], link[end + 1 :], "<", ">"
    parts = link.split()
    if not parts:
        return "", "", "", ""
    target = parts[0]
    suffix = link[len(target) :]
    return target, suffix, "", ""


def _normalize_facet_value(value: str | None) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned


def _extension_from_mime(mime: str) -> str | None:
    ext = mimetypes.guess_extension(mime, strict=False)
    if ext in _EXTENSION_OVERRIDES:
        return _EXTENSION_OVERRIDES[ext]
    return ext


def _parse_data_url(target: str) -> tuple[str, bytes] | None:
    match = _DATA_URL_PATTERN.match(target)
    if not match:
        return None
    mime = match.group(1) or ""
    if not mime.startswith("image/"):
        return None
    if match.group(2) != ";base64":
        return None
    payload = match.group(3) or ""
    try:
        return mime, base64.b64decode(payload)
    except Exception:
        return None


def _is_absolute_url(target: str) -> bool:
    lowered = target.lower()
    return lowered.startswith(("http://", "https://", "data:", "mailto:", "file:", "#")) or target.startswith("/")


def _rewrite_markdown_images(
    markdown: str,
    *,
    source_path: Path,
    images_output_dir: Path,
    written: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []

    def store_bytes(mime: str, data: bytes) -> str | None:
        ext = _extension_from_mime(mime)
        if not ext:
            return None
        digest = _hash_bytes(data)
        filename = f"{digest}{ext}"
        rel = f"images/{filename}"
        if filename not in written:
            images_output_dir.mkdir(parents=True, exist_ok=True)
            dest = images_output_dir / filename
            if not dest.exists():
                dest.write_bytes(data)
            written.add(filename)
        images.append({"path": rel, "sha256": digest, "ext": ext.lstrip("."), "status": "available"})
        return rel

    def store_local(target: str) -> str | None:
        cleaned = target.strip()
        while cleaned.startswith("../"):
            cleaned = cleaned[3:]
        cleaned = cleaned.replace("\\", "/")
        cleaned = cleaned.lstrip("./")
        cleaned = cleaned.lstrip("/")

        local_path = (source_path.parent / cleaned).resolve()
        if local_path.exists() and local_path.is_file():
            ext = local_path.suffix.lower()
            digest = _hash_file(local_path)
            filename = f"{digest}{ext}" if ext else digest
            rel = f"images/{filename}"
            if filename not in written:
                images_output_dir.mkdir(parents=True, exist_ok=True)
                dest = images_output_dir / filename
                if not dest.exists():
                    dest.write_bytes(local_path.read_bytes())
                written.add(filename)
            images.append({"path": rel, "sha256": digest, "ext": ext.lstrip("."), "status": "available"})
            return rel

        images.append({"path": cleaned, "sha256": None, "ext": Path(cleaned).suffix.lstrip("."), "status": "missing"})
        return None

    def replace(match) -> str:
        alt_text = match.group(1)
        raw_link = match.group(2)
        target, suffix, prefix, postfix = _split_link_target(raw_link)
        parsed = _parse_data_url(target)
        if parsed is not None:
            mime, data = parsed
            replacement = store_bytes(mime, data)
            if not replacement:
                return match.group(0)
            new_link = f"{prefix}{replacement}{postfix}{suffix}"
            return f"![{alt_text}]({new_link})"
        if not target or _is_absolute_url(target):
            return match.group(0)

        rel = store_local(target)
        if not rel:
            return match.group(0)
        new_link = f"{prefix}{rel}{postfix}{suffix}"
        return f"![{alt_text}]({new_link})"

    rewritten = _MD_IMAGE_RE.sub(replace, markdown)

    def replace_img(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = _SRC_ATTR_PATTERN.search(tag)
        if not src_match:
            return tag
        raw_value = src_match.group(1)
        quote = ""
        if raw_value and raw_value[0] in {"\"", "'"}:
            quote = raw_value[0]
            value = raw_value[1:-1]
        else:
            value = raw_value
        parsed = _parse_data_url(value)
        if parsed is not None:
            mime, data = parsed
            replacement = store_bytes(mime, data)
        elif not _is_absolute_url(value):
            replacement = store_local(value)
        else:
            replacement = None
        if not replacement:
            return tag
        new_src = f"{quote}{replacement}{quote}" if quote else replacement
        return tag[: src_match.start(1)] + new_src + tag[src_match.end(1) :]

    rewritten = _IMG_TAG_PATTERN.sub(replace_img, rewritten)
    return rewritten, images


def _sanitize_component(value: str) -> str:
    import re

    text = (value or "").strip()
    text = re.sub(r'[\\/:\*\?"<>\|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def _normalize_display_venue(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"\{\{|\}\}", "", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(value: str, max_len: int) -> str:
    if max_len <= 0:
        return value
    return value if len(value) <= max_len else value[:max_len].rstrip("_")


def _folder_names(first_author: str, year: str, title: str, paper_id: str) -> tuple[str, str]:
    base_author = _truncate(_sanitize_component(first_author) or "unknown", 32)
    base_year = _sanitize_component(year) or "unknown"
    base_title = _truncate(_sanitize_component(title) or "untitled", 80)
    full = _sanitize_component(f"{base_author}_{base_year}_{base_title}__{paper_id}")
    short = _sanitize_component(f"{base_author}_{base_year}__{paper_id}")
    if len(full) > 200:
        return short, _sanitize_component(paper_id)
    return full, short


_MONTH_WORDS = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "sept": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}


def _parse_year_month_from_text(text: str) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    value = str(text).strip()
    if not value:
        return None, None
    year_match = re.search(r"(19|20)\d{2}", value)
    year = year_match.group(0) if year_match else None

    numeric_match = re.search(r"(19|20)\d{2}[-/](\d{1,2})", value)
    if numeric_match:
        m = int(numeric_match.group(2))
        month = f"{m:02d}" if 1 <= m <= 12 else None
        return year, month

    word_match = re.search(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
        r"january|february|march|april|june|july|august|september|october|november|december)\b",
        value.lower(),
    )
    if word_match:
        return year, _MONTH_WORDS.get(word_match.group(0))

    return year, None


def _extract_publication_date(paper: dict[str, Any]) -> str:
    value = paper.get("publication_date") or paper.get("paper_publication_date") or ""
    return str(value).strip()


def _load_previous_aliases(db_path: Path) -> dict[str, PreviousAlias]:
    if not db_path:
        return {}
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT paper_key, paper_id, paper_key_type, meta_fingerprint FROM paper_key_alias"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    out: dict[str, PreviousAlias] = {}
    for paper_key, paper_id, paper_key_type, meta_fingerprint in rows:
        out[str(paper_key)] = PreviousAlias(
            paper_id=str(paper_id),
            paper_key_type=str(paper_key_type),
            meta_fingerprint=str(meta_fingerprint) if meta_fingerprint is not None else None,
        )
    return out


def _pick_paper_id(
    candidates: list[PaperKeyCandidate],
    *,
    previous: dict[str, PreviousAlias],
    min_meta_title_similarity: float,
    min_meta_author_jaccard: float,
) -> tuple[str, PaperKeyCandidate, list[str]]:
    preferred = choose_preferred_key(candidates)
    matched: list[tuple[PaperKeyCandidate, PreviousAlias]] = []
    for cand in candidates:
        prev = previous.get(cand.paper_key)
        if prev:
            matched.append((cand, prev))
    if not matched:
        return paper_id_for_key(preferred.paper_key), preferred, []

    matched.sort(key=lambda pair: pair[0].strength, reverse=True)
    chosen_cand, chosen_prev = matched[0]
    conflicts = []
    for cand, prev in matched[1:]:
        if prev.paper_id != chosen_prev.paper_id:
            conflicts.append(
                f"key_conflict:{cand.paper_key} maps {prev.paper_id} vs {chosen_prev.paper_id}"
            )

    if chosen_cand.key_type == "meta":
        if meta_fingerprint_divergent(
            chosen_prev.meta_fingerprint,
            chosen_cand.meta_fingerprint,
            min_title_similarity=min_meta_title_similarity,
            min_author_jaccard=min_meta_author_jaccard,
        ):
            conflicts.append(f"meta_divergent:{chosen_cand.paper_key}")
            return paper_id_for_key(preferred.paper_key), preferred, conflicts

    return chosen_prev.paper_id, preferred, conflicts


def _extract_summary_markdown(paper: dict[str, Any]) -> str:
    if isinstance(paper.get("summary"), str) and paper.get("summary").strip():
        return str(paper.get("summary"))
    templates = paper.get("templates")
    if isinstance(templates, dict):
        for template_tag in ("simple", "simple_phi"):
            tmpl = templates.get(template_tag)
            if isinstance(tmpl, dict) and isinstance(tmpl.get("summary"), str) and tmpl.get("summary").strip():
                return str(tmpl.get("summary"))
    if isinstance(paper.get("abstract"), str) and paper.get("abstract").strip():
        return str(paper.get("abstract"))
    return ""


def _canonical_template_tag(value: str) -> str:
    tag = (value or "").strip().lower()
    tag = re.sub(r"[^a-z0-9_-]+", "_", tag)
    tag = re.sub(r"_+", "_", tag).strip("_")
    return tag or "default"


def _extract_template_summaries(paper: dict[str, Any]) -> dict[str, str]:
    summaries: dict[str, str] = {}
    templates = paper.get("templates")
    if isinstance(templates, dict):
        for tag, payload in templates.items():
            if not isinstance(tag, str) or not tag.strip():
                continue
            canonical_tag = _canonical_template_tag(tag)
            if not isinstance(payload, dict):
                continue
            for key in ("summary", "abstract"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    summaries[canonical_tag] = value.strip()
                    break
            if canonical_tag not in summaries:
                summaries[canonical_tag] = _render_template_fallback_markdown(
                    paper, template_tag=canonical_tag, template_payload=payload
                )

    top_level = paper.get("summary")
    if isinstance(top_level, str) and top_level.strip():
        tag = _canonical_template_tag(
            str(paper.get("default_template") or paper.get("prompt_template") or paper.get("template_tag") or "default")
        )
        summaries.setdefault(tag, top_level.strip())

    if not summaries:
        fallback = _extract_summary_markdown(paper)
        if fallback:
            summaries["default"] = fallback

    return summaries


def _render_template_fallback_markdown(
    paper: dict[str, Any],
    *,
    template_tag: str,
    template_payload: dict[str, Any],
) -> str:
    context = dict(paper)
    context.update(template_payload)
    context.setdefault("output_language", paper.get("output_language") or "en")

    try:
        template = load_render_template(template_tag)
    except Exception:
        template = load_default_template()

    try:
        rendered = template.render(**context)
        return rendered.strip() if isinstance(rendered, str) else ""
    except Exception:
        payload = json.dumps(template_payload, ensure_ascii=False, indent=2)
        return f"```json\n{payload}\n```"


def _choose_preferred_summary_template(paper: dict[str, Any], summaries: dict[str, str]) -> str:
    if not summaries:
        return "default"
    preferred = _canonical_template_tag(str(paper.get("prompt_template") or paper.get("template_tag") or ""))
    if preferred and preferred in summaries:
        return preferred
    for key in ("simple", "simple_phi"):
        if key in summaries:
            return key
    return sorted(summaries.keys(), key=lambda item: item.lower())[0]


def _summary_preview(markdown: str, *, max_len: int = 320) -> str:
    if not markdown:
        return ""
    text = markdown_to_plain_text(markdown)
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_snapshot(opts: SnapshotBuildOptions) -> None:
    if opts.output_db.exists():
        opts.output_db.unlink()

    papers = load_and_merge_papers(
        opts.input_paths,
        opts.bibtex_path,
        cache_dir=None,
        use_cache=False,
        pdf_roots=opts.pdf_roots,
    )
    index = build_index(
        papers,
        md_roots=opts.md_roots,
        md_translated_roots=opts.md_translated_roots,
        pdf_roots=opts.pdf_roots,
    )

    previous_aliases = _load_previous_aliases(opts.previous_snapshot_db) if opts.previous_snapshot_db else {}
    snapshot_build_id = uuid.uuid4().hex

    opts.output_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(opts.output_db))
    conn.row_factory = sqlite3.Row
    try:
        init_snapshot_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_meta(key, value) VALUES (?, ?)",
            ("snapshot_build_id", snapshot_build_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_meta(key, value) VALUES (?, ?)",
            ("built_at", datetime.now(timezone.utc).isoformat()),
        )

        static_root = opts.static_export_dir
        (static_root / "pdf").mkdir(parents=True, exist_ok=True)
        (static_root / "md").mkdir(parents=True, exist_ok=True)
        (static_root / "md_translate").mkdir(parents=True, exist_ok=True)
        (static_root / "images").mkdir(parents=True, exist_ok=True)
        (static_root / "summary").mkdir(parents=True, exist_ok=True)
        (static_root / "manifest").mkdir(parents=True, exist_ok=True)

        written_images: set[str] = set()
        facet_node_cache: dict[tuple[str, str], int] = {}

        def get_facet_node_id(facet_type: str, value: str | None) -> int | None:
            normalized = _normalize_facet_value(value)
            if not normalized or normalized == "unknown":
                return None
            key = (facet_type, normalized)
            cached = facet_node_cache.get(key)
            if cached:
                return cached
            conn.execute(
                "INSERT OR IGNORE INTO facet_node(facet_type, value) VALUES (?, ?)",
                (facet_type, normalized),
            )
            row = conn.execute(
                "SELECT node_id FROM facet_node WHERE facet_type = ? AND value = ?",
                (facet_type, normalized),
            ).fetchone()
            if not row:
                return None
            node_id = int(row["node_id"])
            facet_node_cache[key] = node_id
            return node_id

        with conn:
            for idx, paper in enumerate(index.papers):
                candidates = build_paper_key_candidates(paper)
                paper_id, preferred, conflicts = _pick_paper_id(
                    candidates,
                    previous=previous_aliases,
                    min_meta_title_similarity=opts.min_meta_title_similarity,
                    min_meta_author_jaccard=opts.min_meta_author_jaccard,
                )

                title = str(paper.get("paper_title") or "").strip()
                year = str(paper.get("_year") or "unknown").strip() or "unknown"
                year = year if year.isdigit() else year.lower()
                month = "unknown"
                pub_date = _extract_publication_date(paper)

                bib = paper.get("bibtex") if isinstance(paper.get("bibtex"), dict) else None
                bib_fields = (bib.get("fields") if isinstance(bib, dict) else None) or {}
                bib_year = str(bib_fields.get("year") or "").strip()
                bib_month = str(bib_fields.get("month") or "").strip()
                if bib_year and not year.isdigit():
                    parsed_year, _ = _parse_year_month_from_text(bib_year)
                    if parsed_year:
                        year = parsed_year
                if bib_month:
                    _, parsed_month = _parse_year_month_from_text(f"2000-{bib_month}")
                    if parsed_month:
                        month = parsed_month
                if month == "unknown" and pub_date:
                    _, parsed_month = _parse_year_month_from_text(pub_date)
                    if parsed_month:
                        month = parsed_month

                if not pub_date:
                    pub_date = year if year.isdigit() else ""
                venue = _normalize_display_venue(str(paper.get("_venue") or "").strip()) or "unknown"
                source_hash = str(paper.get("source_hash") or stable_hash(str(paper.get("source_path") or idx)))

                authors = paper.get("_authors") or paper.get("paper_authors") or []
                if not isinstance(authors, list):
                    authors = [str(authors)]
                first_author = str(authors[0]) if authors else "unknown"

                pdf_hash = None
                source_md_hash = None
                translated_hashes: dict[str, str] = {}
                images: list[dict[str, Any]] = []

                md_path = index.md_path_by_hash.get(source_hash)
                if md_path:
                    raw_md = _safe_read_text(md_path)
                    rewritten_md, md_images = _rewrite_markdown_images(
                        raw_md,
                        source_path=md_path,
                        images_output_dir=static_root / "images",
                        written=written_images,
                    )
                    source_md_hash = _hash_text(rewritten_md)
                    md_target = static_root / "md" / f"{source_md_hash}.md"
                    if not md_target.exists():
                        md_target.write_text(rewritten_md, encoding="utf-8")
                    images.extend(md_images)

                translations = index.translated_md_by_hash.get(source_hash, {})
                for lang, t_path in translations.items():
                    raw_md = _safe_read_text(t_path)
                    rewritten_md, md_images = _rewrite_markdown_images(
                        raw_md,
                        source_path=t_path,
                        images_output_dir=static_root / "images",
                        written=written_images,
                    )
                    md_hash = _hash_text(rewritten_md)
                    lang_norm = str(lang).lower()
                    (static_root / "md_translate" / lang_norm).mkdir(parents=True, exist_ok=True)
                    md_target = static_root / "md_translate" / lang_norm / f"{md_hash}.md"
                    if not md_target.exists():
                        md_target.write_text(rewritten_md, encoding="utf-8")
                    translated_hashes[lang_norm] = md_hash
                    images.extend(md_images)

                pdf_path = index.pdf_path_by_hash.get(source_hash)
                if pdf_path:
                    pdf_hash = _hash_file(pdf_path)
                    pdf_target = static_root / "pdf" / f"{pdf_hash}.pdf"
                    if not pdf_target.exists():
                        pdf_target.write_bytes(pdf_path.read_bytes())

                template_summaries = _extract_template_summaries(paper)
                preferred_summary_template = _choose_preferred_summary_template(paper, template_summaries)
                preferred_summary_markdown = template_summaries.get(preferred_summary_template) or ""
                preview_source = template_summaries.get("simple") or preferred_summary_markdown
                summary_preview = _summary_preview(preview_source)

                base_summary_payload = {
                    "paper_id": paper_id,
                    "paper_title": title,
                    "paper_authors": authors,
                    "publication_date": paper.get("publication_date") or "",
                    "publication_venue": _normalize_display_venue(str(paper.get("publication_venue") or venue)),
                    "abstract": paper.get("abstract") or "",
                    "keywords": paper.get("keywords") or paper.get("_keywords") or [],
                    "paper_institutions": paper.get("paper_institutions") or [],
                    "output_language": paper.get("output_language") or "",
                    "provider": paper.get("provider") or "",
                    "model": paper.get("model") or "",
                    "prompt_template": paper.get("prompt_template") or paper.get("template_tag") or "",
                    "extracted_at": paper.get("extracted_at") or "",
                }

                # Back-compat + convenience: summary/<paper_id>.json always exists and points to the preferred template.
                _write_json(
                    static_root / "summary" / f"{paper_id}.json",
                    {
                        **base_summary_payload,
                        "template_tag": preferred_summary_template,
                        "summary": preferred_summary_markdown,
                        "available_templates": sorted(template_summaries.keys(), key=lambda item: item.lower()),
                    },
                )

                # Per-template summary exports.
                summary_dir = static_root / "summary" / paper_id
                for template_tag, summary_markdown in template_summaries.items():
                    _write_json(
                        summary_dir / f"{template_tag}.json",
                        {
                            **base_summary_payload,
                            "template_tag": template_tag,
                            "summary": summary_markdown,
                        },
                    )

                folder_name, folder_name_short = _folder_names(first_author, year, title, paper_id)
                pdf_filename = _sanitize_component(f"{first_author}_{year}_{title}") or f"{paper_id}"
                pdf_filename = _truncate(pdf_filename, 120) + ".pdf"

                manifest_payload = {
                    "paper_id": paper_id,
                    "folder_name": folder_name,
                    "folder_name_short": folder_name_short,
                    "assets": {
                        "pdf": {
                            "static_path": f"pdf/{pdf_hash}.pdf" if pdf_hash else None,
                            "zip_path": pdf_filename if pdf_hash else None,
                            "sha256": pdf_hash,
                        },
                        "source_md": {
                            "static_path": f"md/{source_md_hash}.md" if source_md_hash else None,
                            "zip_path": "source.md" if source_md_hash else None,
                            "sha256": source_md_hash,
                        },
                        "translated_md": [
                            {
                                "lang": lang,
                                "static_path": f"md_translate/{lang}/{md_hash}.md",
                                "zip_path": f"translated/{lang}.md",
                                "sha256": md_hash,
                            }
                            for lang, md_hash in sorted(translated_hashes.items())
                        ],
                        "summary": {
                            "static_path": f"summary/{paper_id}.json",
                            "zip_path": "summary.json",
                        },
                        "summary_templates": [
                            {
                                "template_tag": template_tag,
                                "static_path": f"summary/{paper_id}/{template_tag}.json",
                                "zip_path": f"summaries/{template_tag}.json",
                            }
                            for template_tag in sorted(template_summaries.keys(), key=lambda item: item.lower())
                        ],
                    },
                    "images": [
                        {
                            "static_path": item.get("path"),
                            "zip_path": item.get("path"),
                            "sha256": item.get("sha256"),
                            "ext": item.get("ext"),
                            "status": item.get("status"),
                        }
                        for item in images
                    ],
                    "conflicts": conflicts,
                }
                if images:
                    deduped: dict[str, dict[str, Any]] = {}
                    for item in manifest_payload["images"]:
                        key = str(item.get("static_path") or "")
                        if not key:
                            continue
                        if key not in deduped:
                            deduped[key] = item
                        elif deduped[key].get("status") != "available" and item.get("status") == "available":
                            deduped[key] = item
                    manifest_payload["images"] = list(deduped.values())
                _write_json(static_root / "manifest" / f"{paper_id}.json", manifest_payload)

                conn.execute(
                    """
                    INSERT OR REPLACE INTO paper(
                      paper_id, paper_key, paper_key_type, title, year, month, publication_date, venue, preferred_summary_template, summary_preview, paper_index,
                      source_hash, output_language, provider, model, prompt_template, extracted_at,
                      pdf_content_hash, source_md_content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_id,
                        preferred.paper_key,
                        preferred.key_type,
                        title,
                        year,
                        month,
                        pub_date,
                        venue,
                        preferred_summary_template,
                        summary_preview,
                        0,
                        source_hash,
                        str(paper.get("output_language") or ""),
                        str(paper.get("provider") or ""),
                        str(paper.get("model") or ""),
                        str(paper.get("prompt_template") or paper.get("template_tag") or ""),
                        str(paper.get("extracted_at") or ""),
                        pdf_hash,
                        source_md_hash,
                    ),
                )

                for template_tag in sorted(template_summaries.keys(), key=lambda item: item.lower()):
                    conn.execute(
                        "INSERT OR IGNORE INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                        (paper_id, template_tag),
                    )

                for lang, md_hash in translated_hashes.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                        (paper_id, lang, md_hash),
                    )

                for cand in candidates:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO paper_key_alias(paper_key, paper_id, paper_key_type, meta_fingerprint)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            cand.paper_key,
                            paper_id,
                            cand.key_type,
                            cand.meta_fingerprint if cand.key_type == "meta" else None,
                        ),
                    )

                def upsert_facet(table: str, join_table: str, id_col: str, value: str) -> None:
                    normalized = _normalize_facet_value(value)
                    if not normalized or normalized == "unknown":
                        return
                    conn.execute(
                        f"INSERT OR IGNORE INTO {table}(value) VALUES (?)",
                        (normalized,),
                    )
                    row = conn.execute(
                        f"SELECT {id_col} FROM {table} WHERE value = ?",
                        (normalized,),
                    ).fetchone()
                    if not row:
                        return
                    facet_id = int(row[0])
                    conn.execute(
                        f"INSERT OR IGNORE INTO {join_table}(paper_id, {id_col}) VALUES (?, ?)",
                        (paper_id, facet_id),
                    )

                for author in authors:
                    upsert_facet("author", "paper_author", "author_id", str(author))
                keywords = paper.get("keywords") or paper.get("_keywords") or []
                if isinstance(keywords, list):
                    for kw in keywords:
                        upsert_facet("keyword", "paper_keyword", "keyword_id", str(kw))
                institutions = paper.get("paper_institutions") or []
                if isinstance(institutions, list):
                    for inst in institutions:
                        upsert_facet("institution", "paper_institution", "institution_id", str(inst))
                tags = paper.get("ai_generated_tags") or paper.get("_tags") or []
                if isinstance(tags, list):
                    for tag in tags:
                        upsert_facet("tag", "paper_tag", "tag_id", str(tag))
                upsert_facet("venue", "paper_venue", "venue_id", venue)

                graph_nodes: set[int] = set()

                def add_graph_nodes(facet_type: str, values: Any) -> None:
                    if values is None:
                        return
                    if isinstance(values, (list, tuple, set)):
                        iterable = values
                    else:
                        iterable = [values]
                    for item in iterable:
                        node_id = get_facet_node_id(facet_type, item)
                        if node_id is not None:
                            graph_nodes.add(node_id)

                add_graph_nodes("author", authors)
                if isinstance(keywords, list):
                    add_graph_nodes("keyword", keywords)
                if isinstance(institutions, list):
                    add_graph_nodes("institution", institutions)
                if isinstance(tags, list):
                    add_graph_nodes("tag", tags)
                add_graph_nodes("venue", venue)
                add_graph_nodes("year", year)
                add_graph_nodes("month", month)
                add_graph_nodes("summary_template", list(template_summaries.keys()))
                add_graph_nodes("output_language", paper.get("output_language"))
                add_graph_nodes("provider", paper.get("provider"))
                add_graph_nodes("model", paper.get("model"))
                add_graph_nodes("prompt_template", paper.get("prompt_template") or paper.get("template_tag"))
                add_graph_nodes("translation_lang", list(translated_hashes.keys()))

                for node_id in graph_nodes:
                    conn.execute(
                        "INSERT OR IGNORE INTO paper_facet(paper_id, node_id) VALUES (?, ?)",
                        (paper_id, node_id),
                    )

                node_list = sorted(graph_nodes)
                if len(node_list) > 1:
                    edge_rows = []
                    for idx, left in enumerate(node_list):
                        for right in node_list[idx + 1 :]:
                            edge_rows.append((left, right))
                    conn.executemany(
                        """
                        INSERT INTO facet_edge(node_id_a, node_id_b, paper_count)
                        VALUES (?, ?, 1)
                        ON CONFLICT(node_id_a, node_id_b)
                        DO UPDATE SET paper_count = paper_count + 1
                        """,
                        edge_rows,
                    )

                summary_text = markdown_to_plain_text(" ".join(template_summaries.values()))
                source_text = ""
                translated_text = ""
                if source_md_hash and md_path:
                    source_text = markdown_to_plain_text(_safe_read_text(static_root / "md" / f"{source_md_hash}.md"))
                if translated_hashes:
                    translated_parts: list[str] = []
                    for lang, md_hash in translated_hashes.items():
                        translated_parts.append(
                            markdown_to_plain_text(
                                _safe_read_text(static_root / "md_translate" / lang / f"{md_hash}.md")
                            )
                        )
                    translated_text = " ".join(part for part in translated_parts if part)

                metadata_text = " ".join(
                    part
                    for part in [
                        title,
                        " ".join(str(a) for a in authors),
                        venue,
                        " ".join(str(k) for k in (keywords if isinstance(keywords, list) else [])),
                        " ".join(str(i) for i in (institutions if isinstance(institutions, list) else [])),
                        year,
                    ]
                    if part
                )

                conn.execute(
                    """
                    INSERT INTO paper_fts(paper_id, title, summary, source, translated, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_id,
                        insert_cjk_spaces(title),
                        insert_cjk_spaces(summary_text),
                        insert_cjk_spaces(source_text),
                        insert_cjk_spaces(translated_text),
                        insert_cjk_spaces(metadata_text),
                    ),
                )
                conn.execute(
                    "INSERT INTO paper_fts_trigram(paper_id, title, venue) VALUES (?, ?, ?)",
                    (paper_id, title.lower(), venue.lower()),
                )

            recompute_paper_index(conn)
            recompute_facet_counts(conn)
    finally:
        conn.close()
