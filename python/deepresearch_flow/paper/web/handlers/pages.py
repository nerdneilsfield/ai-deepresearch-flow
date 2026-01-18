"""Page route handlers for paper web UI."""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from deepresearch_flow.paper.db_ops import PaperIndex
from deepresearch_flow.paper.web.markdown import (
    create_md_renderer,
    normalize_markdown_images,
    render_markdown_with_math_placeholders,
    render_paper_markdown,
    select_template_tag,
)
from deepresearch_flow.paper.web.static_assets import resolve_asset_urls
from deepresearch_flow.paper.web.text import normalize_title
from deepresearch_flow.paper.web.templates import (
    build_pdfjs_viewer_url,
    render_template,
)


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _load_markdown_for_view(
    index: PaperIndex,
    asset_config,
    export_dir: Path | None,
    source_hash: str,
    *,
    lang: str | None = None,
) -> str | None:
    if export_dir and asset_config and asset_config.enabled and (asset_config.base_url or "") == "":
        if lang:
            translated_url = asset_config.translated_md_urls.get(source_hash, {}).get(lang.lower())
            if translated_url:
                export_path = export_dir / translated_url.lstrip("/")
                if export_path.exists():
                    return _safe_read_text(export_path)
        else:
            md_url = asset_config.md_urls.get(source_hash)
            if md_url:
                export_path = export_dir / md_url.lstrip("/")
                if export_path.exists():
                    return _safe_read_text(export_path)

    if lang:
        md_path = index.translated_md_by_hash.get(source_hash, {}).get(lang.lower())
    else:
        md_path = index.md_path_by_hash.get(source_hash)
    if not md_path:
        return None
    raw = _safe_read_text(md_path)
    if lang:
        raw = normalize_markdown_images(raw)
    return raw


async def robots_txt(_: Request) -> Response:
    """Serve robots.txt to disallow all crawlers."""
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")


async def index_page(request: Request) -> HTMLResponse:
    """Main landing page with search and paper list."""
    from deepresearch_flow.paper.web.templates import render_template

    index: PaperIndex = request.app.state.index
    filter_help = (
        "Filters syntax:\n"
        "pdf:yes|no source:yes|no translated:yes|no summary:yes|no\n"
        "tmpl:<tag> or template:<tag>\n"
        "has:pdf / no:source aliases\n"
        "Content tags still use the search box (tag:fpga)."
    )
    # Convert newlines to HTML entity for tooltip
    filter_help_escaped = filter_help.replace("\n", "&#10;")

    return HTMLResponse(
        render_template(
            "index.html",
            title="Paper DB",
            template_tags=index.template_tags,
            filter_help=filter_help_escaped,
        )
    )


async def stats_page(request: Request) -> HTMLResponse:
    """Statistics page with charts."""
    from deepresearch_flow.paper.web.templates import render_template

    return HTMLResponse(render_template("stats.html", title="Stats"))


async def paper_detail(request: Request) -> HTMLResponse:
    """Paper detail page with multiple views (summary, source, translated, PDF, etc).

    Uses Jinja2 templates for rendering (detail.html).
    """
    index: PaperIndex = request.app.state.index
    source_hash = request.path_params["source_hash"]
    idx = index.id_by_hash.get(source_hash)
    if idx is None:
        return RedirectResponse("/")
    paper = index.papers[idx]
    is_pdf_only = bool(paper.get("_is_pdf_only"))
    page_title = normalize_title(str(paper.get("paper_title") or "")) or "Paper"
    view = request.query_params.get("view")
    template_param = request.query_params.get("template")
    embed = request.query_params.get("embed") == "1"

    pdf_path = index.pdf_path_by_hash.get(source_hash)
    asset_urls = resolve_asset_urls(
        index,
        source_hash,
        request.app.state.asset_config,
        prefer_local=request.app.state.static_mode == "dev",
    )
    pdf_url = asset_urls["pdf_url"] or ""
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

    # Determine allowed views
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

    # Determine split view settings
    if is_pdf_only:
        default_left = preferred_pdf_view
        default_right = preferred_pdf_view
    else:
        default_left = preferred_pdf_view if pdf_path else ("source" if source_available else "summary")
        default_right = "summary"

    left_param = request.query_params.get("left")
    right_param = request.query_params.get("right")
    left_view = normalize_view(left_param, default_left) if left_param else default_left
    right_view = normalize_view(right_param, default_right) if right_param else default_right

    # Build tabs and view_hrefs
    def build_href(v: str, **extra_params: str) -> str:
        params: dict[str, str] = {"view": v}
        if v == "summary" and template_param:
            params["template"] = str(template_param)
        if v == "translated" and selected_lang:
            params["lang"] = selected_lang
        if v == "split":
            params["left"] = left_view
            params["right"] = right_view
        for k, val in extra_params.items():
            params[k] = str(val)
        return f"/paper/{source_hash}?{urlencode(params)}"

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

    tabs = [(label, v) for label, v in tab_defs if v in allowed_views]
    view_hrefs = {v: build_href(v) for label, v in tab_defs if v in allowed_views}

    # Initialize template variables
    body_html = ""
    summary_template_name = ""
    template_warning = ""
    template_controls = ""
    source_path_str = ""
    translated_path_str = ""
    source_markdown_url = ""
    translated_markdown_url = ""
    images_base_url = asset_urls["images_base_url"] or ""
    pdf_filename = ""
    pdfjs_url = ""
    pdfjs_script_url = ""
    pdfjs_worker_url = ""
    left_src = ""
    right_src = ""
    split_options: list[tuple[str, str]] = []
    show_outline = False

    selected_tag, available_templates = select_template_tag(paper, template_param)

    # Summary view
    if view == "summary":
        markdown, summary_template_name, warning = render_paper_markdown(
            paper,
            request.app.state.fallback_language,
            template_tag=selected_tag,
        )
        md_renderer = create_md_renderer()
        body_html = render_markdown_with_math_placeholders(md_renderer, markdown)
        # Warning is already HTML, don't wrap again
        template_warning = warning if warning else ""
        show_outline = True
        if available_templates:
            options = "\n".join(
                f'<option value="{html.escape(tag)}"{" selected" if tag == selected_tag else ""}>{html.escape(tag)}</option>'
                for tag in available_templates
            )
            template_controls = f"""
<div class="flex items-center gap-2 text-sm text-slate-500">
  <span>Template:</span>
  <select id="templateSelect" class="h-9 rounded-md border border-slate-200 bg-white px-2 text-sm text-slate-900 shadow-sm">
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

    prefer_local = request.app.state.static_mode == "dev"

    # Source view
    if view == "source":
        source_path = index.md_path_by_hash.get(source_hash)
        if not source_path or not asset_urls["md_url"]:
            body_html = '<div class="warning">Source markdown not found. Provide --md-root to enable source viewing.</div>'
        else:
            source_markdown_url = asset_urls["md_url"] or ""
            source_path_str = str(source_path)
            show_outline = True
            if prefer_local:
                raw = _load_markdown_for_view(
                    index,
                    request.app.state.asset_config,
                    request.app.state.static_export_dir,
                    source_hash,
                )
                if raw is not None:
                    md_renderer = create_md_renderer()
                    body_html = render_markdown_with_math_placeholders(md_renderer, raw)

    # Translated view
    if view == "translated":
        if not translation_langs or not selected_lang:
            body_html = '<div class="warning">No translated markdown found. Provide <code>--md-translated-root</code> and place <code><base>.<lang>.md</code> under that root.</div>'
        else:
            translated_path = translations.get(selected_lang)
            translated_markdown_url = asset_urls["md_translated_url"].get(selected_lang, "")
            if not translated_path or not translated_markdown_url:
                body_html = '<div class="warning">Translated markdown not found for the selected language.</div>'
            else:
                translated_path_str = str(translated_path)
                show_outline = True
                if prefer_local:
                    raw = _load_markdown_for_view(
                        index,
                        request.app.state.asset_config,
                        request.app.state.static_export_dir,
                        source_hash,
                        lang=selected_lang,
                    )
                    if raw is not None:
                        md_renderer = create_md_renderer()
                        body_html = render_markdown_with_math_placeholders(md_renderer, raw)

    # PDF view
    if view == "pdf":
        if not pdf_path or not pdf_url:
            body_html = '<div class="warning">PDF not found. Provide --pdf-root to enable PDF viewing.</div>'
        pdf_filename = str(pdf_path.name) if pdf_path else ""
        pdfjs_cdn_base_url = request.app.state.pdfjs_cdn_base_url
        if pdfjs_cdn_base_url:
            pdfjs_script_url = f"{pdfjs_cdn_base_url}/legacy/build/pdf.min.js"
            pdfjs_worker_url = f"{pdfjs_cdn_base_url}/legacy/build/pdf.worker.min.js"
        else:
            pdfjs_script_url = "/pdfjs/build/pdf.js"
            pdfjs_worker_url = "/pdfjs/build/pdf.worker.js"

    # PDF.js view
    if view == "pdfjs":
        if not pdf_path or not pdf_url:
            body_html = '<div class="warning">PDF not found. Provide --pdf-root to enable PDF viewing.</div>'
        pdfjs_url = build_pdfjs_viewer_url(
            pdf_url,
            cdn_base_url=request.app.state.pdfjs_cdn_base_url,
        )
        pdf_filename = str(pdf_path.name) if pdf_path else ""

    # Split view
    if view == "split":
        def pane_src(pane_view: str) -> str:
            if pane_view == "pdfjs" and pdf_path and pdf_url:
                return build_pdfjs_viewer_url(
                    pdf_url,
                    cdn_base_url=request.app.state.pdfjs_cdn_base_url,
                )
            params: dict[str, str] = {"view": pane_view, "embed": "1"}
            if pane_view == "summary" and template_param:
                params["template"] = str(template_param)
            if pane_view == "translated" and selected_lang:
                params["lang"] = selected_lang
            return f"/paper/{source_hash}?{urlencode(params)}"

        left_src = pane_src(left_view)
        right_src = pane_src(right_view)

        split_options = [
            ("summary", "Summary"),
            ("source", "Source"),
            ("translated", "Translated"),
            ("pdf", "PDF"),
            ("pdfjs", "PDF Viewer"),
        ]
        if is_pdf_only:
            split_options = [
                ("pdf", "PDF"),
                ("pdfjs", "PDF Viewer"),
            ]

    # Render template
    container_class = "wide" if view == "split" else ""
    body_class = "font-hei"
    if embed:
        body_class = f"{body_class} embed-view"
    if view == "split":
        body_class = f"{body_class} split-view"
    return HTMLResponse(
        render_template(
            "detail.html",
            title=page_title,
            embed=embed,
            header_title=page_title,
            body_class=body_class,
            container_class=container_class,
            is_pdf_only=is_pdf_only,
            current_view=view,
            tabs=tabs,
            view_hrefs=view_hrefs,
            show_outline=show_outline,
            # Content variables
            body_html=body_html,
            summary_template_name=summary_template_name,
            template_warning=template_warning,
            template_controls=template_controls,
            available_templates=available_templates,
            selected_template_tag=selected_tag,
            images_base_url=images_base_url,
            source_markdown_url=source_markdown_url,
            translated_markdown_url=translated_markdown_url,
            # Source view
            source_path=source_path_str,
            # Translated view
            translated_path=translated_path_str,
            selected_lang=selected_lang,
            translation_langs=translation_langs,
            # PDF view
            pdf_filename=pdf_filename,
            pdf_url=pdf_url,
            pdfjs_script_url=pdfjs_script_url,
            pdfjs_worker_url=pdfjs_worker_url,
            # PDF.js view
            pdfjs_url=pdfjs_url,
            # Split view
            left_src=left_src,
            right_src=right_src,
            left_view=left_view,
            right_view=right_view,
            split_options=split_options,
        )
    )
