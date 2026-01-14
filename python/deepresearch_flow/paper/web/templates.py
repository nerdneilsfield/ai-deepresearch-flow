"""HTML template and shell utilities for paper web UI."""

from __future__ import annotations

import html
from urllib.parse import quote

from deepresearch_flow.paper.web.constants import PDFJS_VIEWER_PATH


def embed_shell(title: str, body_html: str, extra_head: str = "", extra_scripts: str = "") -> str:
    """Generate a simple embedded HTML shell without navigation."""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex, nofollow, noarchive, nosnippet" />
    <meta name="googlebot" content="noindex, nofollow, noarchive, nosnippet" />
    <meta name="bingbot" content="noindex, nofollow, noarchive, nosnippet" />
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; padding: 16px; }}
      h1, h2, h3, h4 {{ margin-top: 1.2em; }}
      .muted {{ color: #57606a; font-size: 13px; }}
      .warning {{ background: #fff4ce; border: 1px solid #ffd089; padding: 10px; border-radius: 10px; margin: 12px 0; }}
      pre {{ overflow: auto; padding: 10px; background: #0b1220; color: #e6edf3; border-radius: 10px; }}
      code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      a {{ color: #0969da; }}
    </style>
    {extra_head}
  </head>
  <body>
    {body_html}
    {extra_scripts}
  </body>
</html>"""


def page_shell(
    title: str,
    body_html: str,
    extra_head: str = "",
    extra_scripts: str = "",
    header_title: str | None = None,
) -> str:
    """Generate a full HTML page shell with navigation header."""
    header_html = """
    <header>
      <a href="/">Papers</a>
      <a href="/stats">Stats</a>
    </header>
"""
    if header_title:
        safe_title = html.escape(header_title)
        header_html = f"""
    <header class="detail-header">
      <div class="header-row">
        <a class="header-back" href="/">← Papers</a>
        <span class="header-title" title="{safe_title}">{safe_title}</span>
        <a class="header-link" href="/stats">Stats</a>
      </div>
    </header>
"""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex, nofollow, noarchive, nosnippet" />
    <meta name="googlebot" content="noindex, nofollow, noarchive, nosnippet" />
    <meta name="bingbot" content="noindex, nofollow, noarchive, nosnippet" />
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; }}
      header {{ position: sticky; top: 0; background: #0b1220; color: #fff; padding: 12px 16px; z-index: 10; }}
      header a {{ color: #cfe3ff; text-decoration: none; margin-right: 12px; }}
      .detail-header .header-row {{ display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; }}
      .detail-header .header-title {{ text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
      .detail-header .header-back {{ margin-right: 0; }}
      .detail-header .header-link {{ margin-right: 0; }}
      .container {{ max-width: 1100px; margin: 0 auto; padding: 16px; }}
      .filters {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; margin: 12px 0 16px; }}
      .filters input {{ width: 100%; padding: 8px; border: 1px solid #d0d7de; border-radius: 6px; }}
      .filters select {{ width: 100%; border: 1px solid #d0d7de; border-radius: 6px; background: #fff; font-size: 13px; }}
      .filters select:not([multiple]) {{ padding: 6px 8px; }}
      .filters select[multiple] {{ padding: 2px; line-height: 1.25; min-height: 72px; font-size: 13px; }}
      .filters select[multiple] option {{ padding: 2px 6px; line-height: 1.25; }}
      .filters label {{ font-size: 12px; color: #57606a; }}
      .filter-group {{ display: flex; flex-direction: column; gap: 4px; }}
      .card {{ border: 1px solid #d0d7de; border-radius: 10px; padding: 12px; margin: 10px 0; }}
      .muted {{ color: #57606a; font-size: 13px; }}
      .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid #d0d7de; margin-right: 6px; font-size: 12px; }}
      .pill.template {{ border-color: #8a92a5; color: #243b53; background: #f6f8fa; }}
      .pill.pdf-only {{ border-color: #c8a951; background: #fff8dc; color: #5b4a00; }}
      .warning {{ background: #fff4ce; border: 1px solid #ffd089; padding: 10px; border-radius: 10px; margin: 12px 0; }}
      .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
      .tab {{ display: inline-block; padding: 6px 12px; border-radius: 999px; border: 1px solid #d0d7de; background: #f6f8fa; color: #0969da; text-decoration: none; font-size: 13px; }}
      .tab:hover {{ background: #eef1f4; }}
      .tab.active {{ background: #0969da; border-color: #0969da; color: #fff; }}
      .detail-shell {{ display: flex; flex-direction: column; gap: 12px; min-height: calc(100vh - 120px); }}
      .detail-toolbar {{ display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-start; gap: 12px; padding: 6px 8px 10px; border-bottom: 1px solid #e5e7eb; box-sizing: border-box; }}
      .detail-toolbar .tabs {{ margin: 0; }}
      .toolbar-actions {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-left: auto; padding-right: 16px; }}
      .search-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; align-items: stretch; }}
      .search-row input {{ flex: 1 1 320px; min-width: 0; padding: 10px; border: 1px solid #d0d7de; border-radius: 8px; }}
      .search-row select {{ flex: 0 1 220px; min-width: 0; max-width: 100%; padding: 10px; border: 1px solid #d0d7de; border-radius: 8px; background: #fff; }}
      .filter-row {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }}
      .filter-row input {{ flex: 1 1 320px; min-width: 0; padding: 10px; border: 1px solid #d0d7de; border-radius: 8px; }}
      .filter-row select {{ flex: 0 1 180px; min-width: 0; padding: 8px 10px; border: 1px solid #d0d7de; border-radius: 8px; background: #fff; }}
      .filter-row .help-icon {{ flex: 0 0 auto; }}
      .adv-actions {{ display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }}
      .split-inline {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }}
      .split-inline select {{ padding: 6px 8px; border-radius: 8px; border: 1px solid #d0d7de; background: #fff; min-width: 140px; }}
      .split-actions {{ display: flex; align-items: center; justify-content: center; gap: 8px; }}
      .split-actions button {{ padding: 6px 10px; border-radius: 999px; border: 1px solid #d0d7de; background: #f6f8fa; cursor: pointer; min-width: 36px; }}
      .lang-select {{ display: flex; align-items: center; gap: 6px; }}
      .lang-select label {{ color: #57606a; font-size: 13px; }}
      .lang-select select {{ padding: 6px 8px; border-radius: 8px; border: 1px solid #d0d7de; background: #fff; min-width: 120px; }}
      .fullscreen-actions {{ display: flex; align-items: center; gap: 6px; }}
      .fullscreen-actions button {{ padding: 6px 10px; border-radius: 8px; border: 1px solid #d0d7de; background: #f6f8fa; cursor: pointer; }}
      .fullscreen-exit {{ display: none; }}
      body.detail-fullscreen {{ overflow: hidden; --outline-top: 16px; }}
      body.detail-fullscreen header {{ display: none; }}
      body.detail-fullscreen .container {{ max-width: 100%; padding: 0; }}
      body.detail-fullscreen .detail-shell {{
        position: fixed;
        inset: 0;
        padding: 12px 16px;
        background: #fff;
        z-index: 40;
        overflow: auto;
      }}
      body.detail-fullscreen .detail-toolbar {{ position: sticky; top: 0; background: #fff; z-index: 41; }}
      body.detail-fullscreen .fullscreen-enter {{ display: none; }}
      body.detail-fullscreen .fullscreen-exit {{ display: inline-flex; }}
      .detail-body {{ display: flex; flex-direction: column; gap: 8px; flex: 1; min-height: 0; }}
      .help-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; border-radius: 50%; border: 1px solid #d0d7de; color: #57606a; font-size: 12px; cursor: default; position: relative; }}
      .help-icon::after {{ content: attr(data-tip); display: none; position: absolute; top: 24px; right: 0; background: #0b1220; color: #e6edf3; padding: 8px 10px; border-radius: 8px; font-size: 12px; white-space: pre-line; width: 260px; z-index: 20; }}
      .help-icon:hover::after {{ display: block; }}
      .stats {{ margin: 12px 0 6px; }}
      .stats-row {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
      .stats-label {{ font-weight: 600; color: #0b1220; margin-right: 4px; }}
      .pill.stat {{ background: #f6f8fa; border-color: #c7d2e0; color: #1f2a37; }}
      .footnotes {{ border-top: 1px solid #e5e7eb; margin-top: 16px; padding-top: 12px; color: #57606a; }}
      .footnotes ol {{ padding-left: 20px; }}
      .footnotes li {{ margin-bottom: 6px; }}
      .footnote-ref {{ font-size: 0.85em; }}
      .footnote-tip {{ position: relative; display: inline-block; }}
      .footnote-tip::after {{
        content: attr(data-footnote);
        position: absolute;
        left: 50%;
        bottom: 130%;
        transform: translateX(-50%);
        width: min(320px, 70vw);
        padding: 8px 10px;
        border-radius: 8px;
        background: #0b1220;
        color: #e6edf3;
        font-size: 12px;
        line-height: 1.35;
        white-space: pre-line;
        box-shadow: 0 10px 24px rgba(0, 0, 0, 0.18);
        opacity: 0;
        pointer-events: none;
        z-index: 30;
        transition: opacity 0.12s ease-in-out;
      }}
      .footnote-tip:hover::after,
      .footnote-tip:focus::after {{
        opacity: 1;
      }}
      pre {{ overflow: auto; padding: 10px; background: #0b1220; color: #e6edf3; border-radius: 10px; }}
      code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      a {{ color: #0969da; }}
      @media (max-width: 640px) {{
        .search-row {{
          flex-direction: column;
        }}
        .search-row input,
        .search-row select {{
          width: 100%;
        }}
        .filter-row {{
          flex-direction: column;
          align-items: stretch;
        }}
        .filter-row .help-icon {{
          align-self: flex-end;
        }}
        .adv-actions {{
          flex-direction: column;
          align-items: stretch;
        }}
        .detail-toolbar {{
          flex-wrap: nowrap;
          overflow-x: auto;
          padding-bottom: 8px;
        }}
        .detail-toolbar::-webkit-scrollbar {{ height: 6px; }}
        .detail-toolbar::-webkit-scrollbar-thumb {{ background: #c7d2e0; border-radius: 999px; }}
        .detail-toolbar .tabs,
        .toolbar-actions {{
          flex: 0 0 auto;
        }}
      }}
    </style>
    {extra_head}
  </head>
  <body>
    {header_html}
    <div class="container">
      {body_html}
    </div>
    {extra_scripts}
  </body>
</html>"""


def build_pdfjs_viewer_url(pdf_url: str) -> str:
    """Build a PDF.js viewer URL for the given PDF URL."""
    encoded = quote(pdf_url, safe="")
    return f"{PDFJS_VIEWER_PATH}?file={encoded}"


def outline_assets(outline_top: str) -> tuple[str, str, str]:
    """Generate outline HTML, CSS, and JavaScript assets."""
    outline_html = """
<div id="outline" style="position:fixed;top:var(--outline-top,96px);right:20px;max-width:260px;max-height:calc(100vh - var(--outline-top,96px) - 60px);overflow-y:auto;padding:12px;background:#ffffff;border:1px solid #d0d7de;border-radius:8px;box-shadow:0 2px 8px rgba(27,31,36,0.08);font-size:13px;z-index:5;display:none;">
  <div style="position:sticky;top:-12px;background:#ffffff;z-index:2;padding:4px 0 6px;">
    <button id="outlineClose" style="float:right;padding:4px 8px;border:1px solid #d0d7de;border-radius:6px;background:#f6f8fa;cursor:pointer;font-size:12px;">Hide</button>
    <strong style="color:#1f2a37;">Outline</strong>
  </div>
  <div id="outlineContent" style="margin-top:8px;"></div>
</div>
<div id="outlineToggleContainer" style="position:fixed;top:var(--outline-top,96px);right:20px;z-index:4;">
  <button id="outlineToggle" style="padding:8px 12px;border:1px solid #d0d7de;border-radius:8px;background:#f6f8fa;cursor:pointer;font-size:13px;box-shadow:0 1px 3px rgba(27,31,36,0.12);">☰ Outline</button>
</div>
"""
    outline_css = """
<style>
#outline::-webkit-scrollbar { width: 6px; }
#outline::-webkit-scrollbar-track { background: transparent; }
#outline::-webkit-scrollbar-thumb { background: #c7d2e0; border-radius: 999px; }
#outlineContent a { display: block; padding: 4px 0; color: #0969da; text-decoration: none; line-height: 1.3; }
#outlineContent a:hover { text-decoration: underline; }
#outlineContent .outline-h2 { padding-left: 0; }
#outlineContent .outline-h3 { padding-left: 12px; font-size: 12px; }
#outlineContent .outline-h4 { padding-left: 24px; font-size: 11px; color: #57606a; }
@media (max-width: 1400px) {
  #outline, #outlineToggleContainer { display: none !important; }
}
</style>
"""
    outline_js = f"""
(function() {{
  const content = document.getElementById('content');
  if (!content) return;
  const headings = content.querySelectorAll('h2, h3, h4');
  if (headings.length === 0) return;

  const outline = document.getElementById('outline');
  const toggle = document.getElementById('outlineToggle');
  const close = document.getElementById('outlineClose');
  const outlineContent = document.getElementById('outlineContent');

  if (!outline || !toggle || !close || !outlineContent) return;

  for (let i = 0; i < headings.length; i++) {{
    const h = headings[i];
    if (!h.id) h.id = 'heading-' + i;
    const a = document.createElement('a');
    a.href = '#' + h.id;
    a.textContent = h.textContent.trim();
    a.className = 'outline-' + h.tagName.toLowerCase();
    outlineContent.appendChild(a);
  }}

  toggle.addEventListener('click', () => {{
    outline.style.display = 'block';
    toggle.style.display = 'none';
  }});

  close.addEventListener('click', () => {{
    outline.style.display = 'none';
    toggle.style.display = 'block';
  }});

  const savedState = sessionStorage.getItem('outlineVisible');
  if (savedState === 'true') {{
    outline.style.display = 'block';
    toggle.style.display = 'none';
  }} else {{
    toggle.style.display = 'block';
  }}

  const observer = new MutationObserver(() => {{
    const isVisible = outline.style.display === 'block';
    sessionStorage.setItem('outlineVisible', String(isVisible));
  }});
  observer.observe(outline, {{ attributes: true, attributeFilter: ['style'] }});
}})();
"""
    return outline_html, outline_css, outline_js
