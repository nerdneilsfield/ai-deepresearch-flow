from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

from markdown_it import MarkdownIt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from deepresearch_flow.paper.render import load_default_template
from deepresearch_flow.paper.template_registry import load_render_template
from deepresearch_flow.paper.utils import stable_hash


_CDN_ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"
_CDN_MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"
_CDN_KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css"
_CDN_KATEX_JS = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"
_CDN_KATEX_AUTO = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"


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


def _split_csv(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


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


def _normalize_month_token(value: str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        if 1 <= value <= 12:
            return f"{value:02d}"
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        return _normalize_month_token(int(raw))
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


def build_index(papers: list[dict[str, Any]]) -> PaperIndex:
    id_by_hash: dict[str, int] = {}
    by_tag: dict[str, set[int]] = {}
    by_author: dict[str, set[int]] = {}
    by_year: dict[str, set[int]] = {}
    by_month: dict[str, set[int]] = {}
    by_venue: dict[str, set[int]] = {}

    year_counts: dict[str, int] = {}
    month_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    author_counts: dict[str, int] = {}
    venue_counts: dict[str, int] = {}

    def add_index(index: dict[str, set[int]], key: str, idx: int) -> None:
        index.setdefault(key, set()).add(idx)

    for idx, paper in enumerate(papers):
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
        year_counts[year_label] = year_counts.get(year_label, 0) + 1
        month_counts[month_label] = month_counts.get(month_label, 0) + 1

        venue = _extract_venue(paper).strip()
        paper["_venue"] = venue
        if venue:
            add_index(by_venue, _normalize_key(venue), idx)
            venue_counts[venue] = venue_counts.get(venue, 0) + 1
        else:
            add_index(by_venue, "unknown", idx)
            venue_counts["Unknown"] = venue_counts.get("Unknown", 0) + 1

        authors = _extract_authors(paper)
        paper["_authors"] = authors
        for author in authors:
            key = _normalize_key(author)
            add_index(by_author, key, idx)
            author_counts[author] = author_counts.get(author, 0) + 1

        tags = _extract_tags(paper)
        paper["_tags"] = tags
        for tag in tags:
            key = _normalize_key(tag)
            add_index(by_tag, key, idx)
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    def year_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, str]:
        idx, paper = item
        year_label = str(paper.get("_year") or "Unknown")
        title_label = str(paper.get("paper_title") or "")
        if year_label.isdigit():
            return (0, -int(year_label), title_label.lower())
        return (1, 0, title_label.lower())

    ordered_ids = [idx for idx, _ in sorted(enumerate(papers), key=year_sort_key)]

    stats = {
        "total": len(papers),
        "years": _sorted_counts(year_counts, numeric_desc=True),
        "months": _sorted_month_counts(month_counts),
        "tags": _sorted_counts(tag_counts),
        "authors": _sorted_counts(author_counts),
        "venues": _sorted_counts(venue_counts),
    }

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


def _load_papers(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _md_renderer() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False, "linkify": True})


def _render_paper_markdown(paper: dict[str, Any], fallback_language: str) -> tuple[str, str, str | None]:
    template_name = paper.get("prompt_template")
    warning = None
    if template_name:
        try:
            template = load_render_template(str(template_name))
        except Exception:
            template = load_default_template()
            warning = "Rendered using default template (missing template)."
            template_name = "default_paper"
    else:
        template = load_default_template()
        warning = "Rendered using default template (no template specified)."
        template_name = "default_paper"

    context = dict(paper)
    if not context.get("output_language"):
        context["output_language"] = fallback_language
    return template.render(**context), str(template_name), warning


def _page_shell(title: str, body_html: str, extra_head: str = "", extra_scripts: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; }}
      header {{ position: sticky; top: 0; background: #0b1220; color: #fff; padding: 12px 16px; z-index: 10; }}
      header a {{ color: #cfe3ff; text-decoration: none; margin-right: 12px; }}
      .container {{ max-width: 1100px; margin: 0 auto; padding: 16px; }}
      .filters {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin: 12px 0 16px; }}
      .filters input {{ width: 100%; padding: 8px; border: 1px solid #d0d7de; border-radius: 6px; }}
      .card {{ border: 1px solid #d0d7de; border-radius: 10px; padding: 12px; margin: 10px 0; }}
      .muted {{ color: #57606a; font-size: 13px; }}
      .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid #d0d7de; margin-right: 6px; font-size: 12px; }}
      .warning {{ background: #fff4ce; border: 1px solid #ffd089; padding: 10px; border-radius: 10px; margin: 12px 0; }}
      pre {{ overflow: auto; padding: 10px; background: #0b1220; color: #e6edf3; border-radius: 10px; }}
      code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      a {{ color: #0969da; }}
    </style>
    {extra_head}
  </head>
  <body>
    <header>
      <a href="/">Papers</a>
      <a href="/stats">Stats</a>
    </header>
    <div class="container">
      {body_html}
    </div>
    {extra_scripts}
  </body>
</html>"""


async def _index_page(request: Request) -> HTMLResponse:
    return HTMLResponse(
        _page_shell(
            "Paper DB",
            """
<h2>Paper Database</h2>
<div class="filters">
  <input id="q" placeholder="Title contains..." />
  <input id="tag" placeholder="Tag (comma separated)" />
  <input id="author" placeholder="Author (comma separated)" />
  <input id="year" placeholder="Year (e.g. 2024)" />
  <input id="month" placeholder="Month (01-12)" />
  <input id="venue" placeholder="Venue contains..." />
</div>
<div id="results"></div>
<div id="loading" class="muted">Loading...</div>
<script>
let page = 1;
let loading = false;
let done = false;

function currentParams(nextPage) {
  const params = new URLSearchParams();
  params.set("page", String(nextPage));
  params.set("page_size", "30");
  const q = document.getElementById("q").value.trim();
  const tag = document.getElementById("tag").value.trim();
  const author = document.getElementById("author").value.trim();
  const year = document.getElementById("year").value.trim();
  const month = document.getElementById("month").value.trim();
  const venue = document.getElementById("venue").value.trim();
  if (q) params.set("q", q);
  if (tag) params.set("tag", tag);
  if (author) params.set("author", author);
  if (year) params.set("year", year);
  if (month) params.set("month", month);
  if (venue) params.set("venue", venue);
  return params;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function renderItem(item) {
  const tags = (item.tags || []).map(t => `<span class="pill">${escapeHtml(t)}</span>`).join("");
  const authors = (item.authors || []).slice(0, 6).map(a => escapeHtml(a)).join(", ");
  const meta = `${escapeHtml(item.year || "")}-${escapeHtml(item.month || "")} · ${escapeHtml(item.venue || "")}`;
  return `
    <div class="card">
      <div><a href="/paper/${encodeURIComponent(item.source_hash)}">${escapeHtml(item.title || "")}</a></div>
      <div class="muted">${authors}</div>
      <div class="muted">${meta}</div>
      <div style="margin-top:6px">${tags}</div>
    </div>
  `;
}

async function loadMore() {
  if (loading || done) return;
  loading = true;
  document.getElementById("loading").textContent = "Loading...";
  const res = await fetch(`/api/papers?${currentParams(page).toString()}`);
  const data = await res.json();
  const results = document.getElementById("results");
  for (const item of data.items) {
    results.insertAdjacentHTML("beforeend", renderItem(item));
  }
  if (!data.has_more) {
    done = true;
    document.getElementById("loading").textContent = "End.";
  } else {
    page += 1;
    document.getElementById("loading").textContent = "Scroll to load more...";
  }
  loading = false;
}

function resetAndLoad() {
  page = 1;
  done = false;
  document.getElementById("results").innerHTML = "";
  loadMore();
}

for (const id of ["q", "tag", "author", "year", "month", "venue"]) {
  document.getElementById(id).addEventListener("change", resetAndLoad);
}

window.addEventListener("scroll", () => {
  if ((window.innerHeight + window.scrollY) >= (document.body.offsetHeight - 600)) {
    loadMore();
  }
});

loadMore();
</script>
""",
        )
    )


def _parse_filters(request: Request) -> dict[str, list[str] | str | int]:
    qp = request.query_params
    page = int(qp.get("page", "1"))
    page_size = int(qp.get("page_size", "30"))
    page = max(1, page)
    page_size = min(max(1, page_size), 200)

    q = qp.get("q", "").strip()
    tag = _split_csv(qp.getlist("tag"))
    author = _split_csv(qp.getlist("author"))
    year = _split_csv(qp.getlist("year"))
    month = _split_csv(qp.getlist("month"))
    venue = _split_csv(qp.getlist("venue"))

    return {
        "page": page,
        "page_size": page_size,
        "q": q,
        "tag": tag,
        "author": author,
        "year": year,
        "month": month,
        "venue": venue,
    }


async def _api_papers(request: Request) -> JSONResponse:
    index: PaperIndex = request.app.state.index
    filters = _parse_filters(request)
    page = int(filters["page"])
    page_size = int(filters["page_size"])
    q = str(filters["q"]).lower()

    def union_ids(mapping: dict[str, set[int]], values: list[str]) -> set[int]:
        out: set[int] = set()
        for value in values:
            key = _normalize_key(value)
            out |= mapping.get(key, set())
        return out

    candidate: set[int] | None = None
    for mapping, values in (
        (index.by_tag, filters["tag"]),
        (index.by_author, filters["author"]),
        (index.by_year, filters["year"]),
        (index.by_month, filters["month"]),
    ):
        values_list = list(values)  # type: ignore[arg-type]
        if not values_list:
            continue
        ids = union_ids(mapping, values_list)
        candidate = ids if candidate is None else (candidate & ids)

    venue_values = list(filters["venue"])  # type: ignore[arg-type]
    if venue_values:
        ids: set[int] = set()
        for idx in index.ordered_ids:
            venue = str(index.papers[idx].get("_venue") or "").lower()
            if any(v.lower() in venue for v in venue_values):
                ids.add(idx)
        candidate = ids if candidate is None else (candidate & ids)

    if candidate is None:
        candidate = set(index.ordered_ids)

    if q:
        candidate = {idx for idx in candidate if q in str(index.papers[idx].get("_title_lc") or "")}

    ordered = [idx for idx in index.ordered_ids if idx in candidate]
    total = len(ordered)
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_ids = ordered[start:end]

    items: list[dict[str, Any]] = []
    for idx in page_ids:
        paper = index.papers[idx]
        items.append(
            {
                "source_hash": paper.get("source_hash") or stable_hash(str(paper.get("source_path") or idx)),
                "title": paper.get("paper_title") or "",
                "authors": paper.get("_authors") or [],
                "year": paper.get("_year") or "",
                "month": paper.get("_month") or "",
                "venue": paper.get("_venue") or "",
                "tags": paper.get("_tags") or [],
            }
        )

    return JSONResponse(
        {
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": end < total,
            "items": items,
        }
    )


async def _paper_detail(request: Request) -> HTMLResponse:
    index: PaperIndex = request.app.state.index
    md = request.app.state.md
    source_hash = request.path_params["source_hash"]
    idx = index.id_by_hash.get(source_hash)
    if idx is None:
        return RedirectResponse("/")
    paper = index.papers[idx]
    markdown, template_name, warning = _render_paper_markdown(paper, request.app.state.fallback_language)
    rendered_html = md.render(markdown)

    warning_html = f'<div class="warning">{html.escape(warning)}</div>' if warning else ""
    title = str(paper.get("paper_title") or "Paper")
    body = f"""
<h2>{html.escape(title)}</h2>
<div class="muted">Template: {html.escape(template_name)}</div>
{warning_html}
<div id="content">{rendered_html}</div>
"""

    extra_head = f'<link rel="stylesheet" href="{_CDN_KATEX}" />'
    extra_scripts = f"""
<script src="{_CDN_MERMAID}"></script>
<script src="{_CDN_KATEX_JS}"></script>
<script src="{_CDN_KATEX_AUTO}"></script>
<script>
// Mermaid: convert fenced code blocks to mermaid divs
document.querySelectorAll('code.language-mermaid').forEach((code) => {{
  const pre = code.parentElement;
  const div = document.createElement('div');
  div.className = 'mermaid';
  div.textContent = code.textContent;
  pre.replaceWith(div);
}});
if (window.mermaid) {{
  mermaid.initialize({{ startOnLoad: false }});
  mermaid.run();
}}
if (window.renderMathInElement) {{
  renderMathInElement(document.getElementById('content'), {{
    delimiters: [
      {{left: '$$', right: '$$', display: true}},
      {{left: '$', right: '$', display: false}},
      {{left: '\\\\(', right: '\\\\)', display: false}},
      {{left: '\\\\[', right: '\\\\]', display: true}}
    ],
    throwOnError: false
  }});
}}
</script>
"""
    return HTMLResponse(_page_shell(title, body, extra_head=extra_head, extra_scripts=extra_scripts))


async def _api_stats(request: Request) -> JSONResponse:
    index: PaperIndex = request.app.state.index
    return JSONResponse(index.stats)


async def _stats_page(request: Request) -> HTMLResponse:
    body = """
<h2>Stats</h2>
<div class="muted">Charts are rendered with ECharts (CDN).</div>
<div id="year" style="width:100%;height:360px"></div>
<div id="month" style="width:100%;height:360px"></div>
<div id="tags" style="width:100%;height:420px"></div>
<div id="authors" style="width:100%;height:420px"></div>
<div id="venues" style="width:100%;height:420px"></div>
"""
    scripts = f"""
<script src="{_CDN_ECHARTS}"></script>
<script>
async function main() {{
  const res = await fetch('/api/stats');
  const data = await res.json();

  function bar(el, title, items) {{
    const chart = echarts.init(document.getElementById(el));
    const labels = items.map(x => x.label);
    const counts = items.map(x => x.count);
    chart.setOption({{
      title: {{ text: title }},
      tooltip: {{ trigger: 'axis' }},
      xAxis: {{ type: 'category', data: labels }},
      yAxis: {{ type: 'value' }},
      series: [{{ type: 'bar', data: counts }}]
    }});
  }}

  bar('year', 'Publication Year', data.years || []);
  bar('month', 'Publication Month', data.months || []);
  bar('tags', 'Top Tags', (data.tags || []).slice(0, 20));
  bar('authors', 'Top Authors', (data.authors || []).slice(0, 20));
  bar('venues', 'Top Venues', (data.venues || []).slice(0, 20));
}}
main();
</script>
"""
    return HTMLResponse(_page_shell("Stats", body, extra_scripts=scripts))


def create_app(*, db_path: Path, fallback_language: str = "en") -> Starlette:
    papers = _load_papers(db_path)
    index = build_index(papers)
    md = _md_renderer()
    routes = [
        Route("/", _index_page, methods=["GET"]),
        Route("/stats", _stats_page, methods=["GET"]),
        Route("/paper/{source_hash:str}", _paper_detail, methods=["GET"]),
        Route("/api/papers", _api_papers, methods=["GET"]),
        Route("/api/stats", _api_stats, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.index = index
    app.state.md = md
    app.state.fallback_language = fallback_language
    return app
