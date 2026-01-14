"""Page route handlers for paper web UI."""

from __future__ import annotations

import html
import json
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from deepresearch_flow.paper.db_ops import PaperIndex
from deepresearch_flow.paper.web.constants import (
    CDN_ECHARTS,
    CDN_KATEX,
    CDN_KATEX_AUTO,
    CDN_KATEX_JS,
    CDN_MERMAID,
    CDN_PDFJS,
    CDN_PDFJS_WORKER,
)
from deepresearch_flow.paper.web.markdown import (
    create_md_renderer,
    normalize_markdown_images,
    render_markdown_with_math_placeholders,
    render_paper_markdown,
    select_template_tag,
)
from deepresearch_flow.paper.web.templates import (
    build_pdfjs_viewer_url,
    embed_shell,
    outline_assets,
    page_shell,
)


async def robots_txt(_: Request) -> Response:
    """Serve robots.txt to disallow all crawlers."""
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")


async def index_page(request: Request) -> HTMLResponse:
    """Main landing page with search and paper list."""
    index: PaperIndex = request.app.state.index
    template_options = "".join(
        f'<option value="{html.escape(tag)}">{html.escape(tag)}</option>'
        for tag in index.template_tags
    )
    if not template_options:
        template_options = '<option value="" disabled>(no templates)</option>'
    filter_help = (
        "Filters syntax:\\n"
        "pdf:yes|no source:yes|no translated:yes|no summary:yes|no\\n"
        "tmpl:<tag> or template:<tag>\\n"
        "has:pdf / no:source aliases\\n"
        "Content tags still use the search box (tag:fpga)."
    )
    filter_help_attr = html.escape(filter_help).replace("\n", "&#10;")
    body_html = """
<h2>Paper Database</h2>
<div class="card">
  <div class="muted">Search (Scholar-style): <code>tag:fpga year:2023..2025 -survey</code> · Use quotes for phrases and <code>OR</code> for alternatives.</div>
  <div class="search-row">
    <input id="query" placeholder='Search... e.g. title:"nearest neighbor" tag:fpga year:2023..2025' />
    <select id="openView">
      <option value="summary" selected>Open: Summary</option>
      <option value="source">Open: Source</option>
      <option value="translated">Open: Translated</option>
      <option value="pdf">Open: PDF</option>
      <option value="pdfjs">Open: PDF Viewer</option>
      <option value="split">Open: Split</option>
    </select>
  </div>
  <div class="filters" style="margin-top:10px;">
    <div class="filter-group">
      <label>PDF</label>
      <select id="filterPdf" multiple size="2">
        <option value="with">With</option>
        <option value="without">Without</option>
      </select>
    </div>
    <div class="filter-group">
      <label>Source</label>
      <select id="filterSource" multiple size="2">
        <option value="with">With</option>
        <option value="without">Without</option>
      </select>
    </div>
    <div class="filter-group">
      <label>Translated</label>
      <select id="filterTranslated" multiple size="2">
        <option value="with">With</option>
        <option value="without">Without</option>
      </select>
    </div>
    <div class="filter-group">
      <label>Summary</label>
      <select id="filterSummary" multiple size="2">
        <option value="with">With</option>
        <option value="without">Without</option>
      </select>
    </div>
    <div class="filter-group">
      <label>Template</label>
      <select id="filterTemplate" multiple size="4">
        __TEMPLATE_OPTIONS__
      </select>
    </div>
  </div>
  <div class="filter-row">
    <input id="filterQuery" placeholder='Filters... e.g. pdf:yes tmpl:simple' />
    <select id="sortBy">
      <option value="" selected>Sort: Default</option>
      <option value="year">Sort: Year</option>
      <option value="title">Sort: Title</option>
      <option value="venue">Sort: Venue</option>
      <option value="author">Sort: First Author</option>
    </select>
    <select id="sortDir">
      <option value="desc" selected>Order: Desc</option>
      <option value="asc">Order: Asc</option>
    </select>
    <span class="help-icon" data-tip="__FILTER_HELP__">?</span>
  </div>
  <details style="margin-top:10px;">
    <summary>Advanced search</summary>
    <div style="margin-top:10px;" class="muted">Build a query:</div>
    <div class="filters">
      <input id="advTitle" placeholder="title contains..." />
      <input id="advAuthor" placeholder="author contains..." />
      <input id="advTag" placeholder="tag (comma separated)" />
      <input id="advYear" placeholder="year (e.g. 2020..2024)" />
      <input id="advMonth" placeholder="month (01-12)" />
      <input id="advVenue" placeholder="venue contains..." />
    </div>
    <div class="adv-actions">
      <button id="buildQuery" style="padding:8px 12px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">Build</button>
      <div class="muted">Generated: <code id="generated"></code></div>
    </div>
  </details>
</div>
<div id="stats" class="stats">
  <div id="statsTotal" class="stats-row"></div>
  <div id="statsFiltered" class="stats-row" style="margin-top:6px;"></div>
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
  const q = document.getElementById("query").value.trim();
  if (q) params.set("q", q);
  const fq = document.getElementById("filterQuery").value.trim();
  if (fq) params.set("fq", fq);
  const sortBy = document.getElementById("sortBy").value;
  if (sortBy) params.set("sort_by", sortBy);
  const sortDir = document.getElementById("sortDir").value;
  if (sortDir) params.set("sort_dir", sortDir);
  function addMulti(id, key) {
    const el = document.getElementById(id);
    const values = Array.from(el.selectedOptions).map(opt => opt.value).filter(Boolean);
    for (const value of values) {
      params.append(key, value);
    }
  }
  addMulti("filterPdf", "pdf");
  addMulti("filterSource", "source");
  addMulti("filterTranslated", "translated");
  addMulti("filterSummary", "summary");
  addMulti("filterTemplate", "template");
  return params;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function viewSuffixForItem(item) {
  let view = document.getElementById("openView").value;
  const isPdfOnly = item.is_pdf_only;
  const pdfFallback = item.has_pdf ? "pdfjs" : "pdf";
  if (isPdfOnly && (view === "summary" || view === "source" || view === "translated")) {
    view = pdfFallback;
  }
  if (!view || view === "summary") return "";
  const params = new URLSearchParams();
  params.set("view", view);
  if (view === "split") {
    if (isPdfOnly) {
      params.set("left", pdfFallback);
      params.set("right", pdfFallback);
    } else {
      params.set("left", "summary");
      if (item.has_pdf) {
        params.set("right", "pdfjs");
      } else if (item.has_source) {
        params.set("right", "source");
      } else {
        params.set("right", "summary");
      }
    }
  }
  return `?${params.toString()}`;
}

function renderItem(item) {
  const tags = (item.tags || []).map(t => `<span class="pill">${escapeHtml(t)}</span>`).join("");
  const templateTags = (item.template_tags || []).map(t => `<span class="pill template">tmpl:${escapeHtml(t)}</span>`).join("");
  const authors = (item.authors || []).slice(0, 6).map(a => escapeHtml(a)).join(", ");
  const meta = `${escapeHtml(item.year || "")}-${escapeHtml(item.month || "")} · ${escapeHtml(item.venue || "")}`;
  const viewSuffix = viewSuffixForItem(item);
  const badges = [
    item.has_source ? `<span class="pill">source</span>` : "",
    item.has_translation ? `<span class="pill">translated</span>` : "",
    item.has_pdf ? `<span class="pill">pdf</span>` : "",
    item.is_pdf_only ? `<span class="pill pdf-only">pdf-only</span>` : "",
  ].join("");
  return `
    <div class="card">
      <div><a href="/paper/${encodeURIComponent(item.source_hash)}${viewSuffix}">${escapeHtml(item.title || "")}</a></div>
      <div class="muted">${authors}</div>
      <div class="muted">${meta}</div>
      <div style="margin-top:6px">${badges} ${templateTags} ${tags}</div>
    </div>
  `;
}

function renderStatsRow(targetId, label, counts) {
  const row = document.getElementById(targetId);
  if (!row || !counts) return;
  const pills = [];
  pills.push(`<span class="stats-label">${escapeHtml(label)}</span>`);
  pills.push(`<span class="pill stat">Count ${counts.total}</span>`);
  pills.push(`<span class="pill stat">PDF ${counts.pdf}</span>`);
  pills.push(`<span class="pill stat">Source ${counts.source}</span>`);
  pills.push(`<span class="pill stat">Translated ${counts.translated || 0}</span>`);
  pills.push(`<span class="pill stat">Summary ${counts.summary}</span>`);
  const order = counts.template_order || Object.keys(counts.templates || {});
  for (const tag of order) {
    const count = (counts.templates && counts.templates[tag]) || 0;
    pills.push(`<span class="pill stat">tmpl:${escapeHtml(tag)} ${count}</span>`);
  }
  row.innerHTML = pills.join("");
}

function updateStats(stats) {
  if (!stats) return;
  renderStatsRow("statsTotal", "Total", stats.all);
  renderStatsRow("statsFiltered", "Filtered", stats.filtered);
}

async function loadMore() {
  if (loading || done) return;
  loading = true;
  document.getElementById("loading").textContent = "Loading...";
  const res = await fetch(`/api/papers?${currentParams(page).toString()}`);
  const data = await res.json();
  if (data.stats) {
    updateStats(data.stats);
  }
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

document.getElementById("query").addEventListener("change", resetAndLoad);
document.getElementById("openView").addEventListener("change", resetAndLoad);
document.getElementById("filterQuery").addEventListener("change", resetAndLoad);
document.getElementById("filterPdf").addEventListener("change", resetAndLoad);
document.getElementById("filterSource").addEventListener("change", resetAndLoad);
document.getElementById("filterTranslated").addEventListener("change", resetAndLoad);
document.getElementById("filterSummary").addEventListener("change", resetAndLoad);
document.getElementById("filterTemplate").addEventListener("change", resetAndLoad);
document.getElementById("sortBy").addEventListener("change", resetAndLoad);
document.getElementById("sortDir").addEventListener("change", resetAndLoad);

document.getElementById("buildQuery").addEventListener("click", () => {
  function add(field, value) {
    value = value.trim();
    if (!value) return "";
    if (value.includes(" ")) return `${field}:"${value}"`;
    return `${field}:${value}`;
  }
  const parts = [];
  const t = document.getElementById("advTitle").value.trim();
  const a = document.getElementById("advAuthor").value.trim();
  const tag = document.getElementById("advTag").value.trim();
  const y = document.getElementById("advYear").value.trim();
  const m = document.getElementById("advMonth").value.trim();
  const v = document.getElementById("advVenue").value.trim();
  if (t) parts.push(add("title", t));
  if (a) parts.push(add("author", a));
  if (tag) {
    for (const item of tag.split(",")) {
      const val = item.trim();
      if (val) parts.push(add("tag", val));
    }
  }
  if (y) parts.push(add("year", y));
  if (m) parts.push(add("month", m));
  if (v) parts.push(add("venue", v));
  const q = parts.join(" ");
  document.getElementById("generated").textContent = q;
  document.getElementById("query").value = q;
  resetAndLoad();
});

window.addEventListener("scroll", () => {
  if ((window.innerHeight + window.scrollY) >= (document.body.offsetHeight - 600)) {
    loadMore();
  }
});

loadMore();
</script>
"""
    body_html = body_html.replace("__TEMPLATE_OPTIONS__", template_options)
    body_html = body_html.replace("__FILTER_HELP__", filter_help_attr)
    return HTMLResponse(page_shell("Paper DB", body_html))


async def stats_page(request: Request) -> HTMLResponse:
    """Statistics page with charts."""
    body = """
<h2>Stats</h2>
<div class="muted">Charts are rendered with ECharts (CDN).</div>
<div id="year" style="width:100%;height:360px"></div>
<div id="month" style="width:100%;height:360px"></div>
<div id="tags" style="width:100%;height:420px"></div>
<div id="keywords" style="width:100%;height:420px"></div>
<div id="authors" style="width:100%;height:420px"></div>
<div id="venues" style="width:100%;height:420px"></div>
"""
    scripts = f"""
<script src="{CDN_ECHARTS}"></script>
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
  bar('keywords', 'Top Keywords', (data.keywords || []).slice(0, 20));
  bar('authors', 'Top Authors', (data.authors || []).slice(0, 20));
  bar('venues', 'Top Venues', (data.venues || []).slice(0, 20));
}}
main();
</script>
"""
    return HTMLResponse(page_shell("Stats", body, extra_scripts=scripts))


async def paper_detail(request: Request) -> HTMLResponse:
    """Paper detail page with multiple views (summary, source, translated, PDF, etc)."""
    index: PaperIndex = request.app.state.index
    md = request.app.state.md
    source_hash = request.path_params["source_hash"]
    idx = index.id_by_hash.get(source_hash)
    if idx is None:
        return RedirectResponse("/")
    paper = index.papers[idx]
    is_pdf_only = bool(paper.get("_is_pdf_only"))
    page_title = str(paper.get("paper_title") or "Paper")
    view = request.query_params.get("view")
    template_param = request.query_params.get("template")
    embed = request.query_params.get("embed") == "1"

    pdf_path = index.pdf_path_by_hash.get(source_hash)
    pdf_url = f"/api/pdf/{source_hash}"
    source_available = source_hash in index.md_path_by_hash
    translations = index.translated_md_by_hash.get(source_hash, {})
    translation_langs = sorted(translations.keys(), key=str.lower)
    lang_param = request.query_params.get("lang")
    normalized_lang = lang_param.lower() if lang_param else None
    selected_lang = None
    if translation_langs:
        if normalized_lang and normalized_lang in translations:
            selected_lang = normalized_lang
        elif "zh" in translations:
            selected_lang = "zh"
        else:
            selected_lang = translation_langs[0]
    allowed_views = {"summary", "source", "translated", "pdf", "pdfjs", "split"}
    if is_pdf_only:
        allowed_views = {"pdf", "pdfjs", "split"}

    def normalize_view(value: str | None, default: str) -> str:
        if value in allowed_views:
            return value
        return default

    preferred_pdf_view = "pdfjs" if pdf_path else "pdf"
    default_view = preferred_pdf_view if is_pdf_only else "summary"
    view = normalize_view(view, default_view)
    if view == "split":
        embed = False
    if is_pdf_only:
        left_param = request.query_params.get("left")
        right_param = request.query_params.get("right")
        left = normalize_view(left_param, preferred_pdf_view) if left_param else preferred_pdf_view
        right = normalize_view(right_param, preferred_pdf_view) if right_param else preferred_pdf_view
    else:
        default_left = preferred_pdf_view if pdf_path else ("source" if source_available else "summary")
        default_right = "summary"
        left_param = request.query_params.get("left")
        right_param = request.query_params.get("right")
        left = normalize_view(left_param, default_left) if left_param else default_left
        right = normalize_view(right_param, default_right) if right_param else default_right

    def render_page(title: str, body: str, extra_head: str = "", extra_scripts: str = "") -> HTMLResponse:
        if embed:
            return HTMLResponse(embed_shell(title, body, extra_head, extra_scripts))
        return HTMLResponse(page_shell(title, body, extra_head, extra_scripts, header_title=page_title))

    def nav_link(label: str, v: str) -> str:
        active = " active" if view == v else ""
        params: dict[str, str] = {"view": v}
        if v == "summary" and template_param:
            params["template"] = str(template_param)
        if v == "translated" and selected_lang:
            params["lang"] = selected_lang
        if v == "split":
            params["left"] = left
            params["right"] = right
        href = f"/paper/{source_hash}?{urlencode(params)}"
        return f'<a class="tab{active}" href="{html.escape(href)}">{html.escape(label)}</a>'

    tab_defs = [
        ("Summary", "summary"),
        ("Source", "source"),
        ("Translated", "translated"),
        ("PDF", "pdf"),
        ("PDF Viewer", "pdfjs"),
        ("Split", "split"),
    ]
    if is_pdf_only:
        tab_defs = [
            ("PDF", "pdf"),
            ("PDF Viewer", "pdfjs"),
            ("Split", "split"),
        ]
    tabs_html = '<div class="tabs">' + "".join(nav_link(label, v) for label, v in tab_defs) + "</div>"
    fullscreen_controls = """
<div class="fullscreen-actions">
  <button id="fullscreenEnter" class="fullscreen-enter" type="button" title="Enter fullscreen">Fullscreen</button>
  <button id="fullscreenExit" class="fullscreen-exit" type="button" title="Exit fullscreen">Exit Fullscreen</button>
</div>
"""

    def detail_toolbar(extra_controls: str = "") -> str:
        if embed:
            return ""
        controls = extra_controls.strip()
        toolbar_controls = f"{controls}{fullscreen_controls}" if controls else fullscreen_controls
        return f"""
<div class="detail-toolbar">
  {tabs_html}
  <div class="toolbar-actions">
    {toolbar_controls}
  </div>
</div>
"""

    def wrap_detail(content: str, toolbar_html: str | None = None) -> str:
        if embed:
            return content
        toolbar = detail_toolbar() if toolbar_html is None else toolbar_html
        return f"""
<div class="detail-shell">
  {toolbar}
  <div class="detail-body">
    {content}
  </div>
</div>
"""

    fullscreen_script = ""
    if not embed:
        fullscreen_script = """
<script>
const fullscreenEnter = document.getElementById('fullscreenEnter');
const fullscreenExit = document.getElementById('fullscreenExit');
function setFullscreen(enable) {
  document.body.classList.toggle('detail-fullscreen', enable);
}
if (fullscreenEnter) {
  fullscreenEnter.addEventListener('click', () => setFullscreen(true));
}
if (fullscreenExit) {
  fullscreenExit.addEventListener('click', () => setFullscreen(false));
}
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && document.body.classList.contains('detail-fullscreen')) {
    setFullscreen(false);
  }
});
</script>
"""
    pdf_only_warning_html = ""
    if is_pdf_only:
        pdf_only_warning_html = (
            '<div class="warning">PDF-only entry: summary and source views are unavailable.</div>'
        )
    outline_top = "72px" if not embed else "16px"
    outline_html, outline_css, outline_js = outline_assets(outline_top)

    if view == "split":
        def pane_src(pane_view: str) -> str:
            if pane_view == "pdfjs" and pdf_path:
                return build_pdfjs_viewer_url(pdf_url)
            params: dict[str, str] = {"view": pane_view, "embed": "1"}
            if pane_view == "summary" and template_param:
                params["template"] = str(template_param)
            if pane_view == "translated" and selected_lang:
                params["lang"] = selected_lang
            return f"/paper/{source_hash}?{urlencode(params)}"

        left_src = pane_src(left)
        right_src = pane_src(right)
        options = [
            ("summary", "Summary"),
            ("source", "Source"),
            ("translated", "Translated"),
            ("pdf", "PDF"),
            ("pdfjs", "PDF Viewer"),
        ]
        if is_pdf_only:
            options = [
                ("pdf", "PDF"),
                ("pdfjs", "PDF Viewer"),
            ]
        if translation_langs:
            lang_options = "\n".join(
                f'<option value="{html.escape(lang)}"{" selected" if lang == selected_lang else ""}>'
                f'{html.escape(lang)}</option>'
                for lang in translation_langs
            )
            lang_disabled = ""
        else:
            lang_options = '<option value="" selected>(no translations)</option>'
            lang_disabled = " disabled"
        left_options = "\n".join(
            f'<option value="{value}"{" selected" if value == left else ""}>{label}</option>'
            for value, label in options
        )
        right_options = "\n".join(
            f'<option value="{value}"{" selected" if value == right else ""}>{label}</option>'
            for value, label in options
        )
        split_controls = f"""
<div class="split-inline">
  <span class="muted">Left</span>
  <select id="splitLeft">
    {left_options}
  </select>
  <div class="split-actions">
    <button id="splitTighten" type="button" title="Tighten width">-</button>
    <button id="splitSwap" type="button" title="Swap panes">⇄</button>
    <button id="splitWiden" type="button" title="Widen width">+</button>
  </div>
  <span class="muted">Right</span>
  <select id="splitRight">
    {right_options}
  </select>
  <span class="muted">Lang</span>
  <select id="splitLang"{lang_disabled}>
    {lang_options}
  </select>
</div>
"""
        toolbar_html = detail_toolbar(split_controls)
        split_layout = f"""
{pdf_only_warning_html}
<div class="split-layout">
  <div class="split-pane">
    <iframe id="leftPane" src="{html.escape(left_src)}" title="Left pane"></iframe>
  </div>
  <div class="split-pane">
    <iframe id="rightPane" src="{html.escape(right_src)}" title="Right pane"></iframe>
  </div>
</div>
"""
        body = wrap_detail(split_layout, toolbar_html=toolbar_html)
        extra_head = """
<style>
.container {
  max-width: 100%;
  width: 100%;
  margin: 0 auto;
}
.split-layout {
  display: flex;
  gap: 12px;
  width: 100%;
  max-width: var(--split-max-width, 100%);
  margin: 0 auto;
  flex: 1;
  min-height: 440px;
}
.split-pane {
  flex: 1;
  border: 1px solid #d0d7de;
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}
.split-pane iframe {
  width: 100%;
  height: 100%;
  border: 0;
}
@media (max-width: 900px) {
  .split-layout {
    flex-direction: column;
    min-height: 0;
  }
  .split-pane {
    height: 70vh;
  }
}
</style>
"""
        extra_scripts = """
<script>
const leftSelect = document.getElementById('splitLeft');
const rightSelect = document.getElementById('splitRight');
const langSelect = document.getElementById('splitLang');
const swapButton = document.getElementById('splitSwap');
const tightenButton = document.getElementById('splitTighten');
const widenButton = document.getElementById('splitWiden');
function updateSplit() {
  const params = new URLSearchParams(window.location.search);
  params.set('view', 'split');
  params.set('left', leftSelect.value);
  params.set('right', rightSelect.value);
  if (langSelect && langSelect.value) {
    params.set('lang', langSelect.value);
  }
  window.location.search = params.toString();
}
leftSelect.addEventListener('change', updateSplit);
rightSelect.addEventListener('change', updateSplit);
if (langSelect) {
  langSelect.addEventListener('change', updateSplit);
}
swapButton.addEventListener('click', () => {
  const leftValue = leftSelect.value;
  leftSelect.value = rightSelect.value;
  rightSelect.value = leftValue;
  updateSplit();
});
const widthSteps = ["1200px", "1400px", "1600px", "1800px", "2000px", "100%"];
let widthIndex = widthSteps.length - 1;
try {
  const stored = localStorage.getItem('splitWidthIndex');
  if (stored !== null) {
    const parsed = Number.parseInt(stored, 10);
    if (!Number.isNaN(parsed)) {
      widthIndex = Math.max(0, Math.min(widthSteps.length - 1, parsed));
    }
  }
} catch (err) {
  // Ignore storage errors (e.g. private mode)
}

function applySplitWidth() {
  const value = widthSteps[widthIndex];
  document.documentElement.style.setProperty('--split-max-width', value);
  try {
    localStorage.setItem('splitWidthIndex', String(widthIndex));
  } catch (err) {
    // Ignore storage errors
  }
}

tightenButton.addEventListener('click', () => {
  widthIndex = Math.max(0, widthIndex - 1);
  applySplitWidth();
});
widenButton.addEventListener('click', () => {
  widthIndex = Math.min(widthSteps.length - 1, widthIndex + 1);
  applySplitWidth();
});
applySplitWidth();
</script>
"""
        return render_page(
            "Split View",
            body,
            extra_head=extra_head,
            extra_scripts=extra_scripts + fullscreen_script,
        )

    if view == "translated":
        if translation_langs:
            lang_options = "\n".join(
                f'<option value="{html.escape(lang)}"{" selected" if lang == selected_lang else ""}>'
                f'{html.escape(lang)}</option>'
                for lang in translation_langs
            )
            disabled_attr = ""
        else:
            lang_options = '<option value="" selected>(no translations)</option>'
            disabled_attr = " disabled"
        lang_controls = f"""
<div class="lang-select">
  <label for="translationLang">Language</label>
  <select id="translationLang"{disabled_attr}>
    {lang_options}
  </select>
</div>
"""
        toolbar_html = detail_toolbar(lang_controls)
        if not translation_langs or not selected_lang:
            body = wrap_detail(
                '<div class="warning">No translated markdown found. '
                'Provide <code>--md-translated-root</code> and place '
                '<code>&lt;base&gt;.&lt;lang&gt;.md</code> under that root.</div>',
                toolbar_html=toolbar_html,
            )
            return render_page("Translated", body, extra_scripts=fullscreen_script)
        translated_path = translations.get(selected_lang)
        if not translated_path:
            body = wrap_detail(
                '<div class="warning">Translated markdown not found for the selected language.</div>',
                toolbar_html=toolbar_html,
            )
            return render_page("Translated", body, extra_scripts=fullscreen_script)
        try:
            raw = translated_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = translated_path.read_text(encoding="latin-1")
        raw = normalize_markdown_images(raw)
        md_renderer = create_md_renderer()
        rendered = render_markdown_with_math_placeholders(md_renderer, raw)
        body = wrap_detail(
            f"""
<div class="muted">Language: {html.escape(selected_lang)}</div>
<div class="muted">{html.escape(str(translated_path))}</div>
<div class="muted" style="margin-top:10px;">Rendered from translated markdown:</div>
{outline_html}
<div id="content">{rendered}</div>
<details style="margin-top:12px;"><summary>Raw markdown</summary>
  <pre><code>{html.escape(raw)}</code></pre>
</details>
""",
            toolbar_html=toolbar_html,
        )
        extra_head = f"""
<link rel="stylesheet" href="{CDN_KATEX}" />
{outline_css}
<style>
#content img {{
  max-width: 100%;
  height: auto;
}}
</style>
"""
        extra_scripts = f"""
<script src="{CDN_MERMAID}"></script>
<script src="{CDN_KATEX_JS}"></script>
<script src="{CDN_KATEX_AUTO}"></script>
<script>
const translationSelect = document.getElementById('translationLang');
if (translationSelect) {{
  translationSelect.addEventListener('change', () => {{
    const params = new URLSearchParams(window.location.search);
    params.set('view', 'translated');
    params.set('lang', translationSelect.value);
    window.location.search = params.toString();
  }});
}}

(function() {{
  function initRendering() {{
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
  }}
  
  // Run immediately if document is already loaded, otherwise wait
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initRendering);
  }} else {{
    initRendering();
  }}
}})();

if (document.querySelector('.footnotes')) {{
  const notes = {{}};
  document.querySelectorAll('.footnotes li[id]').forEach((li) => {{
    const id = li.getAttribute('id');
    if (!id) return;
    const clone = li.cloneNode(true);
    clone.querySelectorAll('a.footnote-backref').forEach((el) => el.remove());
    const text = (clone.textContent || '').replace(/\\s+/g, ' ').trim();
    if (text) notes['#' + id] = text.length > 400 ? text.slice(0, 397) + '…' : text;
  }});
  document.querySelectorAll('.footnote-ref a[href^="#fn"]').forEach((link) => {{
    const ref = link.getAttribute('href');
    const text = notes[ref];
    if (!text) return;
    link.dataset.footnote = text;
    link.classList.add('footnote-tip');
  }});
}}
{outline_js}
</script>
"""
        return render_page(
            "Translated",
            body,
            extra_head=extra_head,
            extra_scripts=extra_scripts + fullscreen_script,
        )

    if view == "source":
        source_path = index.md_path_by_hash.get(source_hash)
        if not source_path:
            body = wrap_detail(
                '<div class="warning">Source markdown not found. Provide --md-root to enable source viewing.</div>'
            )
            return render_page("Source", body, extra_scripts=fullscreen_script)
        try:
            raw = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = source_path.read_text(encoding="latin-1")
        md_renderer = create_md_renderer()
        rendered = render_markdown_with_math_placeholders(md_renderer, raw)
        body = wrap_detail(
            f"""
<div class="muted">{html.escape(str(source_path))}</div>
<div class="muted" style="margin-top:10px;">Rendered from source markdown:</div>
{outline_html}
<div id="content">{rendered}</div>
<details style="margin-top:12px;"><summary>Raw markdown</summary>
  <pre><code>{html.escape(raw)}</code></pre>
</details>
"""
        )
        extra_head = f"""
<link rel="stylesheet" href="{CDN_KATEX}" />
{outline_css}
<style>
#content img {{
  max-width: 100%;
  height: auto;
}}
</style>
"""
        extra_scripts = f"""
<script src="{CDN_MERMAID}"></script>
<script src="{CDN_KATEX_JS}"></script>
<script src="{CDN_KATEX_AUTO}"></script>
<script>
(function() {{
  function initRendering() {{
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
  }}
  
  // Run immediately if document is already loaded, otherwise wait
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initRendering);
  }} else {{
    initRendering();
  }}
}})();

if (document.querySelector('.footnotes')) {{
  const notes = {{}};
  document.querySelectorAll('.footnotes li[id]').forEach((li) => {{
    const id = li.getAttribute('id');
    if (!id) return;
    const clone = li.cloneNode(true);
    clone.querySelectorAll('a.footnote-backref').forEach((el) => el.remove());
    const text = (clone.textContent || '').replace(/\\s+/g, ' ').trim();
    if (text) notes['#' + id] = text.length > 400 ? text.slice(0, 397) + '…' : text;
  }});
  document.querySelectorAll('.footnote-ref a[href^="#fn"]').forEach((link) => {{
    const ref = link.getAttribute('href');
    const text = notes[ref];
    if (!text) return;
    link.dataset.footnote = text;
    link.classList.add('footnote-tip');
  }});
}}
{outline_js}
</script>
"""
        return render_page("Source", body, extra_head=extra_head, extra_scripts=extra_scripts + fullscreen_script)

    if view == "pdf":
        if not pdf_path:
            body = wrap_detail('<div class="warning">PDF not found. Provide --pdf-root to enable PDF viewing.</div>')
            return render_page("PDF", body, extra_scripts=fullscreen_script)
        body = wrap_detail(
            f"""
{pdf_only_warning_html}
<div class="muted">{html.escape(str(pdf_path.name))}</div>
<div style="display:flex; gap:8px; align-items:center; margin: 10px 0;">
  <button id="prev" style="padding:6px 10px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">Prev</button>
  <button id="next" style="padding:6px 10px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">Next</button>
  <span class="muted">Page <span id="page_num">1</span> / <span id="page_count">?</span></span>
  <span style="flex:1"></span>
  <button id="zoomOut" style="padding:6px 10px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">-</button>
  <button id="zoomIn" style="padding:6px 10px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">+</button>
</div>
<canvas id="the-canvas" style="width: 100%; border: 1px solid #d0d7de; border-radius: 10px;"></canvas>
"""
        )
        extra_scripts = f"""
<script src="{CDN_PDFJS}"></script>
<script>
const url = {json.dumps(pdf_url)};
pdfjsLib.GlobalWorkerOptions.workerSrc = {json.dumps(CDN_PDFJS_WORKER)};
let pdfDoc = null;
let pageNum = 1;
let pageRendering = false;
let pageNumPending = null;
let zoomLevel = 1.0;
const canvas = document.getElementById('the-canvas');
const ctx = canvas.getContext('2d');

function renderPage(num) {{
  pageRendering = true;
  pdfDoc.getPage(num).then((page) => {{
    const baseViewport = page.getViewport({{scale: 1}});
    const containerWidth = canvas.clientWidth || baseViewport.width;
    const fitScale = containerWidth / baseViewport.width;
    const scale = fitScale * zoomLevel;

    const viewport = page.getViewport({{scale}});
    const outputScale = window.devicePixelRatio || 1;

    canvas.width = Math.floor(viewport.width * outputScale);
    canvas.height = Math.floor(viewport.height * outputScale);
    canvas.style.width = Math.floor(viewport.width) + 'px';
    canvas.style.height = Math.floor(viewport.height) + 'px';

    const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null;
    const renderContext = {{ canvasContext: ctx, viewport, transform }};
    const renderTask = page.render(renderContext);
    renderTask.promise.then(() => {{
      pageRendering = false;
      document.getElementById('page_num').textContent = String(pageNum);
      if (pageNumPending !== null) {{
        const next = pageNumPending;
        pageNumPending = null;
        renderPage(next);
      }}
    }});
  }});
}}

function queueRenderPage(num) {{
  if (pageRendering) {{
    pageNumPending = num;
  }} else {{
    renderPage(num);
  }}
}}

function onPrevPage() {{
  if (pageNum <= 1) return;
  pageNum--;
  queueRenderPage(pageNum);
}}

function onNextPage() {{
  if (pageNum >= pdfDoc.numPages) return;
  pageNum++;
  queueRenderPage(pageNum);
}}

function adjustZoom(delta) {{
  zoomLevel = Math.max(0.5, Math.min(3.0, zoomLevel + delta));
  queueRenderPage(pageNum);
}}

document.getElementById('prev').addEventListener('click', onPrevPage);
document.getElementById('next').addEventListener('click', onNextPage);
document.getElementById('zoomOut').addEventListener('click', () => adjustZoom(-0.1));
document.getElementById('zoomIn').addEventListener('click', () => adjustZoom(0.1));

pdfjsLib.getDocument(url).promise.then((pdfDoc_) => {{
  pdfDoc = pdfDoc_;
  document.getElementById('page_count').textContent = String(pdfDoc.numPages);
  renderPage(pageNum);
}});

let resizeTimer = null;
window.addEventListener('resize', () => {{
  if (!pdfDoc) return;
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => queueRenderPage(pageNum), 150);
}});
</script>
"""
        return render_page("PDF", body, extra_scripts=extra_scripts + fullscreen_script)

    if view == "pdfjs":
        if not pdf_path:
            body = wrap_detail('<div class="warning">PDF not found. Provide --pdf-root to enable PDF viewing.</div>')
            return render_page("PDF Viewer", body, extra_scripts=fullscreen_script)
        viewer_url = build_pdfjs_viewer_url(pdf_url)
        frame_height = "calc(100vh - 32px)" if embed else "100%"
        body = wrap_detail(
            f"""
{pdf_only_warning_html}
<div class="muted">{html.escape(str(pdf_path.name))}</div>
<iframe class="pdfjs-frame" src="{html.escape(viewer_url)}" title="PDF.js Viewer"></iframe>
"""
        )
        extra_head = f"""
<style>
.pdfjs-frame {{
  width: 100%;
  height: {frame_height};
  border: 1px solid #d0d7de;
  border-radius: 10px;
  flex: 1;
}}
</style>
"""
        return render_page("PDF Viewer", body, extra_head=extra_head, extra_scripts=fullscreen_script)

    selected_tag, available_templates = select_template_tag(paper, template_param)
    markdown, template_name, warning = render_paper_markdown(
        paper,
        request.app.state.fallback_language,
        template_tag=selected_tag,
    )
    md_renderer = create_md_renderer()
    rendered_html = render_markdown_with_math_placeholders(md_renderer, markdown)

    warning_html = f'<div class="warning">{html.escape(warning)}</div>' if warning else ""
    template_controls = f'<div class="muted">Template: {html.escape(template_name)}</div>'
    if available_templates:
        options = "\n".join(
            f'<option value="{html.escape(tag)}"{" selected" if tag == selected_tag else ""}>{html.escape(tag)}</option>'
            for tag in available_templates
        )
        template_controls = f"""
<div class="muted" style="margin: 6px 0;">
  Template:
  <select id="templateSelect" style="padding:6px 8px; border:1px solid #d0d7de; border-radius:6px;">
    {options}
  </select>
</div>
<script>
const templateSelect = document.getElementById('templateSelect');
if (templateSelect) {{
  templateSelect.addEventListener('change', () => {{
    const params = new URLSearchParams(window.location.search);
    params.set('view', 'summary');
    params.set('template', templateSelect.value);
    window.location.search = params.toString();
  }});
}}
</script>
"""
    content_html = f"""
{template_controls}
{warning_html}
{outline_html}
<div id="content">{rendered_html}</div>
"""
    body = wrap_detail(content_html)

    extra_head = f"""
<link rel="stylesheet" href="{CDN_KATEX}" />
{outline_css}
"""
    extra_scripts = f"""
<script src="{CDN_MERMAID}"></script>
<script src="{CDN_KATEX_JS}"></script>
<script src="{CDN_KATEX_AUTO}"></script>
<script>
(function() {{
  function initRendering() {{
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
  }}
  
  // Run immediately if document is already loaded, otherwise wait
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initRendering);
  }} else {{
    initRendering();
  }}
}})();

if (document.querySelector('.footnotes')) {{
  const notes = {{}};
  document.querySelectorAll('.footnotes li[id]').forEach((li) => {{
    const id = li.getAttribute('id');
    if (!id) return;
    const clone = li.cloneNode(true);
    clone.querySelectorAll('a.footnote-backref').forEach((el) => el.remove());
    const text = (clone.textContent || '').replace(/\\s+/g, ' ').trim();
    if (text) notes['#' + id] = text.length > 400 ? text.slice(0, 397) + '…' : text;
  }});
  document.querySelectorAll('.footnote-ref a[href^="#fn"]').forEach((link) => {{
    const ref = link.getAttribute('href');
    const text = notes[ref];
    if (!text) return;
    link.dataset.footnote = text;
    link.classList.add('footnote-tip');
  }});
}}
{outline_js}
</script>
"""
    return render_page(page_title, body, extra_head=extra_head, extra_scripts=extra_scripts + fullscreen_script)
