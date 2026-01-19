"""Markdown rendering utilities for paper web UI."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from markdown_it import MarkdownIt

try:
    from mdit_py_plugins.footnote import footnote_plugin as footnote
except ImportError:  # pragma: no cover - compatibility with older names
    from mdit_py_plugins.footnote import footnote

from deepresearch_flow.paper.db_ops import _available_templates
from deepresearch_flow.paper.render import load_default_template
from deepresearch_flow.paper.template_registry import load_render_template
from deepresearch_flow.paper.web.text import normalize_venue

_HTML_TABLE_TOKEN_RE = re.compile(r"@@HTML_TABLE_\d+@@")


def create_md_renderer() -> MarkdownIt:
    """Create a configured markdown renderer."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": True})
    md.use(footnote)
    md.enable("table")
    return md


def strip_paragraph_wrapped_tables(text: str) -> str:
    """Remove paragraph tags wrapping table rows."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        line = re.sub(r"^\s*<p>\s*\|", "|", line)
        line = re.sub(r"\|\s*</p>\s*$", "|", line)
        lines[idx] = line
    return "\n".join(lines)


def normalize_footnote_definitions(text: str) -> str:
    """Normalize footnotes and numbered notes to markdown-it footnote format."""
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    in_notes = False
    notes_level: int | None = None
    notes_heading_re = re.compile(
        r"^#{1,6}\s*(参考文献|参考资料|参考书目|文献|引用|注释|脚注|notes?|references?|bibliography|works\s+cited|citations?)\b",
        re.IGNORECASE,
    )
    notes_heading_plain_re = re.compile(
        r"^(参考文献|参考资料|参考书目|文献|引用|注释|脚注|notes?|references?|bibliography|works\s+cited|citations?)\s*:?$",
        re.IGNORECASE,
    )
    last_note_index: int | None = None

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            run_len = 0
            while run_len < len(stripped) and stripped[run_len] == stripped[0]:
                run_len += 1
            if not in_fence:
                in_fence = True
                fence_char = stripped[0]
                fence_len = run_len
            elif stripped[0] == fence_char and run_len >= fence_len:
                in_fence = False
                fence_char = ""
                fence_len = 0
            out.append(line)
            continue

        if in_fence:
            out.append(line)
            continue

        heading_match = notes_heading_re.match(stripped)
        if heading_match:
            in_notes = True
            notes_level = len(stripped.split(" ")[0].lstrip("#"))
            last_note_index = None
        elif notes_heading_plain_re.match(stripped):
            in_notes = True
            notes_level = None
            last_note_index = None
        elif re.match(r"^#{1,6}\s+", stripped):
            if notes_level is not None:
                level = len(stripped.split(" ")[0].lstrip("#"))
                if level <= notes_level:
                    in_notes = False
                    notes_level = None
                    last_note_index = None

        match = re.match(r"^\[\^([0-9]+)\]\s+", line)
        if match:
            out.append(re.sub(r"^\[\^([0-9]+)\]\s+", r"[^\1]: ", line))
            continue

        if in_notes:
            list_match = re.match(r"^\s*(\d{1,4})[.)]\s+", line)
            if list_match:
                number = list_match.group(1)
                rest = line[list_match.end() :].strip()
                out.append(f"[^{number}]: {rest}")
                last_note_index = len(out) - 1
                continue
            if last_note_index is not None:
                if line.strip() == "":
                    out.append(line)
                    last_note_index = None
                    continue
                if line.startswith((" ", "\t")):
                    out[last_note_index] = f"{out[last_note_index]} {line.strip()}"
                    continue

        line = re.sub(r"(?<!\^)\[(\d{1,4})\]", r"[^\1]", line)
        out.append(line)

    return "\n".join(out)


def normalize_markdown_images(text: str) -> str:
    """Normalize markdown images to ensure proper rendering."""
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    img_re = re.compile(r"!\[[^\]]*\]\((?:[^)\\]|\\.)*\)")
    list_re = re.compile(r"^\s{0,3}(-|\*|\+|\d{1,9}\.)\s+")

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            run_len = 0
            while run_len < len(stripped) and stripped[run_len] == stripped[0]:
                run_len += 1
            if not in_fence:
                in_fence = True
                fence_char = stripped[0]
                fence_len = run_len
            elif stripped[0] == fence_char and run_len >= fence_len:
                in_fence = False
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        match = img_re.search(line)
        if not match:
            out.append(line)
            continue
        if list_re.match(line) or (line.lstrip().startswith("|") and line.count("|") >= 2):
            out.append(line)
            continue
        prefix = line[:match.start()]
        if prefix.strip():
            out.append(prefix.rstrip())
            out.append("")
            out.append(line[match.start():].lstrip())
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(line)
    return "\n".join(out)


def normalize_fenced_code_blocks(text: str) -> str:
    """Ensure fenced code block markers appear on their own lines."""
    fence_re = re.compile(r"(`{3,}|~{3,})")
    out: list[str] = []
    for line in text.splitlines():
        match = fence_re.search(line)
        if not match:
            out.append(line)
            continue
        prefix = line[: match.start()]
        suffix = line[match.start() :]
        if prefix.strip():
            out.append(prefix.rstrip())
            out.append(suffix.lstrip())
        else:
            out.append(line)
    return "\n".join(out)


def normalize_mermaid_blocks(text: str) -> str:
    """Keep mermaid fences clean by moving legend text outside the block."""
    lines = text.splitlines()
    out: list[str] = []
    in_mermaid = False
    fence_char = ""
    fence_len = 0
    mermaid_lines: list[str] = []
    legend_lines: list[str] = []

    def is_legend(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith("图例") or stripped.lower().startswith("legend"):
            return True
        return "节点定位" in stripped

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            run_len = 0
            while run_len < len(stripped) and stripped[run_len] == stripped[0]:
                run_len += 1
            rest = stripped[run_len:].strip()
            if not in_mermaid and rest.lower().startswith("mermaid"):
                in_mermaid = True
                fence_char = stripped[0]
                fence_len = run_len
                mermaid_lines = []
                legend_lines = []
                out.append(line)
                continue
            if in_mermaid and stripped[0] == fence_char and run_len >= fence_len and rest == "":
                out.extend(mermaid_lines)
                out.append(line)
                out.extend(legend_lines)
                in_mermaid = False
                fence_char = ""
                fence_len = 0
                mermaid_lines = []
                legend_lines = []
                continue
            out.append(line)
            continue

        if in_mermaid:
            if is_legend(line):
                legend_lines.append(line)
            else:
                mermaid_lines.append(line)
            continue

        out.append(line)

    if in_mermaid:
        out.extend(mermaid_lines)
        out.extend(legend_lines)

    return "\n".join(out)


def normalize_unbalanced_fences(text: str) -> str:
    """Drop unmatched opening fences so later content still renders."""
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    fence_has_content = False
    fence_open_indices: list[int] = []
    fence_re = re.compile(r"([`~]{3,})(.*)$")

    for line in lines:
        stripped = line.lstrip(" ")
        leading_spaces = len(line) - len(stripped)
        is_fence = False
        if leading_spaces <= 3 and stripped:
            match = fence_re.match(stripped)
            if match:
                run = match.group(1)
                fence = run[0]
                run_len = len(run)
                rest = match.group(2) or ""
                has_info = bool(rest.strip())
                if not in_fence:
                    in_fence = True
                    fence_char = fence
                    fence_len = run_len
                    fence_has_content = False
                    fence_open_indices.append(len(out))
                    is_fence = True
                elif fence == fence_char and run_len >= fence_len and not has_info:
                    if not fence_has_content:
                        if fence_open_indices:
                            out.pop(fence_open_indices[-1])
                            fence_open_indices.pop()
                        in_fence = True
                        fence_char = fence
                        fence_len = run_len
                        fence_has_content = False
                        fence_open_indices.append(len(out))
                        is_fence = True
                    else:
                        in_fence = False
                        fence_char = ""
                        fence_len = 0
                        fence_has_content = False
                        is_fence = True
                elif fence == fence_char and run_len >= fence_len and has_info:
                    if fence_open_indices:
                        out.pop(fence_open_indices[-1])
                        fence_open_indices.pop()
                    in_fence = True
                    fence_char = fence
                    fence_len = run_len
                    fence_has_content = False
                    fence_open_indices.append(len(out))
                    is_fence = True

        out.append(line)
        if in_fence and not is_fence and line.strip():
            fence_has_content = True

    if in_fence and fence_open_indices:
        out.pop(fence_open_indices[-1])
    return "\n".join(out)


def extract_math_placeholders(text: str) -> tuple[str, dict[str, str]]:
    """Extract math expressions and replace with placeholders."""
    placeholders: dict[str, str] = {}
    out: list[str] = []
    idx = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    inline_delim_len = 0

    def next_placeholder(value: str) -> str:
        key = f"@@MATH_{len(placeholders)}@@"
        placeholders[key] = value
        return key

    while idx < len(text):
        at_line_start = idx == 0 or text[idx - 1] == "\n"

        if inline_delim_len == 0 and at_line_start:
            line_end = text.find("\n", idx)
            if line_end == -1:
                line_end = len(text)
            line = text[idx:line_end]
            stripped = line.lstrip(" ")
            leading_spaces = len(line) - len(stripped)
            if leading_spaces <= 3 and stripped:
                first = stripped[0]
                if first in {"`", "~"}:
                    run_len = 0
                    while run_len < len(stripped) and stripped[run_len] == first:
                        run_len += 1
                    if run_len >= 3:
                        if not in_fence:
                            in_fence = True
                            fence_char = first
                            fence_len = run_len
                        elif first == fence_char and run_len >= fence_len:
                            in_fence = False
                            fence_char = ""
                            fence_len = 0
                        out.append(line)
                        idx = line_end
                        continue

        if in_fence:
            out.append(text[idx])
            idx += 1
            continue

        if inline_delim_len > 0:
            delim = "`" * inline_delim_len
            if text.startswith(delim, idx):
                out.append(delim)
                idx += inline_delim_len
                inline_delim_len = 0
                continue
            out.append(text[idx])
            idx += 1
            continue

        ch = text[idx]
        if ch == "`":
            run_len = 0
            while idx + run_len < len(text) and text[idx + run_len] == "`":
                run_len += 1
            inline_delim_len = run_len
            out.append("`" * run_len)
            idx += run_len
            continue

        # Block math: $$...$$ (can span lines)
        if text.startswith("$$", idx) and (idx == 0 or text[idx - 1] != "\\"):
            search_from = idx + 2
            end = text.find("$$", search_from)
            while end != -1 and text[end - 1] == "\\":
                search_from = end + 2
                end = text.find("$$", search_from)
            if end != -1:
                out.append(next_placeholder(text[idx : end + 2]))
                idx = end + 2
                continue

        # Inline math: $...$ (single-line)
        if ch == "$" and not text.startswith("$$", idx) and (idx == 0 or text[idx - 1] != "\\"):
            line_end = text.find("\n", idx + 1)
            if line_end == -1:
                line_end = len(text)
            search_from = idx + 1
            end = text.find("$", search_from, line_end)
            while end != -1 and text[end - 1] == "\\":
                search_from = end + 1
                end = text.find("$", search_from, line_end)
            if end != -1:
                out.append(next_placeholder(text[idx : end + 1]))
                idx = end + 1
                continue

        out.append(ch)
        idx += 1

    return "".join(out), placeholders


class _TableSanitizer(HTMLParser):
    """HTML parser for sanitizing table HTML."""
    
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t not in {
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "th",
            "td",
            "caption",
            "colgroup",
            "col",
            "br",
        }:
            return

        allowed: dict[str, str] = {}
        for name, value in attrs:
            if value is None:
                continue
            n = name.lower()
            v = value.strip()
            if t in {"td", "th"} and n in {"colspan", "rowspan"} and v.isdigit():
                allowed[n] = v
            elif t in {"td", "th"} and n == "align" and v.lower() in {"left", "right", "center"}:
                allowed[n] = v.lower()

        attr_text = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in allowed.items())
        self._out.append(f"<{t}{attr_text}>")
        if t not in {"br", "col"}:
            self._stack.append(t)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t not in self._stack:
            return
        while self._stack:
            popped = self._stack.pop()
            self._out.append(f"</{popped}>")
            if popped == t:
                break

    def handle_data(self, data: str) -> None:
        self._out.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._out.append(f"&#{name};")

    def close(self) -> None:
        super().close()
        while self._stack:
            self._out.append(f"</{self._stack.pop()}>")

    def get_html(self) -> str:
        return "".join(self._out)


def sanitize_table_html(raw: str) -> str:
    """Sanitize table HTML to only allow safe elements and attributes."""
    parser = _TableSanitizer()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return f"<pre><code>{html.escape(raw)}</code></pre>"
    return parser.get_html()


def sanitize_img_html(raw: str) -> str | None:
    """Sanitize image HTML to only allow base64 data images."""
    attrs = {}
    for match in re.finditer(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", raw):
        name = match.group(1).lower()
        value = match.group(2).strip()
        if value and value[0] in {"\"", "'"} and value[-1] == value[0]:
            value = value[1:-1]
        attrs[name] = value

    src = attrs.get("src", "")
    src_lower = src.lower()
    if not src_lower.startswith("data:image/") or ";base64," not in src_lower:
        return None

    alt = attrs.get("alt", "")
    alt_attr = f' alt="{html.escape(alt, quote=True)}"' if alt else ""
    return f'<img src="{html.escape(src, quote=True)}"{alt_attr} />'


def extract_html_img_placeholders(text: str) -> tuple[str, dict[str, str]]:
    """Extract HTML img tags and replace with placeholders."""
    placeholders: dict[str, str] = {}
    out: list[str] = []
    idx = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    inline_delim_len = 0

    def next_placeholder(value: str) -> str:
        key = f"@@HTML_IMG_{len(placeholders)}@@"
        placeholders[key] = value
        return key

    lower = text.lower()
    while idx < len(text):
        at_line_start = idx == 0 or text[idx - 1] == "\n"

        if inline_delim_len == 0 and at_line_start:
            line_end = text.find("\n", idx)
            if line_end == -1:
                line_end = len(text)
            line = text[idx:line_end]
            stripped = line.lstrip(" ")
            leading_spaces = len(line) - len(stripped)
            if leading_spaces <= 3 and stripped:
                first = stripped[0]
                if first in {"`", "~"}:
                    run_len = 0
                    while run_len < len(stripped) and stripped[run_len] == first:
                        run_len += 1
                    if run_len >= 3:
                        if not in_fence:
                            in_fence = True
                            fence_char = first
                            fence_len = run_len
                        elif first == fence_char and run_len >= fence_len:
                            in_fence = False
                            fence_char = ""
                            fence_len = 0
                        out.append(line)
                        idx = line_end
                        continue

        if in_fence:
            out.append(text[idx])
            idx += 1
            continue

        if inline_delim_len > 0:
            delim = "`" * inline_delim_len
            if text.startswith(delim, idx):
                out.append(delim)
                idx += inline_delim_len
                inline_delim_len = 0
                continue
            out.append(text[idx])
            idx += 1
            continue

        if text[idx] == "`":
            run_len = 0
            while idx + run_len < len(text) and text[idx + run_len] == "`":
                run_len += 1
            inline_delim_len = run_len
            out.append("`" * run_len)
            idx += run_len
            continue

        if lower.startswith("<img", idx):
            end = text.find(">", idx)
            if end != -1:
                raw = text[idx : end + 1]
                safe_html = sanitize_img_html(raw)
                if safe_html:
                    out.append(next_placeholder(safe_html))
                    idx = end + 1
                    continue

        out.append(text[idx])
        idx += 1

    return "".join(out), placeholders


def extract_html_table_placeholders(text: str) -> tuple[str, dict[str, str]]:
    """Extract HTML table tags and replace with placeholders."""
    placeholders: dict[str, str] = {}
    out: list[str] = []
    idx = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    inline_delim_len = 0

    def next_placeholder(value: str) -> str:
        key = f"@@HTML_TABLE_{len(placeholders)}@@"
        placeholders[key] = value
        return key

    lower = text.lower()
    while idx < len(text):
        at_line_start = idx == 0 or text[idx - 1] == "\n"

        if inline_delim_len == 0 and at_line_start:
            line_end = text.find("\n", idx)
            if line_end == -1:
                line_end = len(text)
            line = text[idx:line_end]
            stripped = line.lstrip(" ")
            leading_spaces = len(line) - len(stripped)
            if leading_spaces <= 3 and stripped:
                first = stripped[0]
                if first in {"`", "~"}:
                    run_len = 0
                    while run_len < len(stripped) and stripped[run_len] == first:
                        run_len += 1
                    if run_len >= 3:
                        if not in_fence:
                            in_fence = True
                            fence_char = first
                            fence_len = run_len
                        elif first == fence_char and run_len >= fence_len:
                            in_fence = False
                            fence_char = ""
                            fence_len = 0
                        out.append(line)
                        idx = line_end
                        continue

        if in_fence:
            out.append(text[idx])
            idx += 1
            continue

        if inline_delim_len > 0:
            delim = "`" * inline_delim_len
            if text.startswith(delim, idx):
                out.append(delim)
                idx += inline_delim_len
                inline_delim_len = 0
                continue
            out.append(text[idx])
            idx += 1
            continue

        if text[idx] == "`":
            run_len = 0
            while idx + run_len < len(text) and text[idx + run_len] == "`":
                run_len += 1
            inline_delim_len = run_len
            out.append("`" * run_len)
            idx += run_len
            continue

        if lower.startswith("<table", idx):
            end = lower.find("</table>", idx)
            if end != -1:
                end += len("</table>")
                raw = text[idx:end]
                key = next_placeholder(raw)
                if out and not out[-1].endswith("\n"):
                    out.append("\n\n")
                out.append(key)
                out.append("\n\n")
                idx = end
                continue

        out.append(text[idx])
        idx += 1

    return "".join(out), placeholders


def render_markdown_with_math_placeholders(md: MarkdownIt, text: str) -> str:
    """Render markdown with math, images, and tables properly escaped."""
    text = normalize_mermaid_blocks(text)
    text = normalize_fenced_code_blocks(text)
    text = normalize_unbalanced_fences(text)
    text = strip_paragraph_wrapped_tables(text)
    text = normalize_footnote_definitions(text)
    rendered, table_placeholders = extract_html_table_placeholders(text)
    rendered, img_placeholders = extract_html_img_placeholders(rendered)
    rendered, placeholders = extract_math_placeholders(rendered)
    html_out = md.render(rendered)
    for key, value in placeholders.items():
        html_out = html_out.replace(key, html.escape(value))
    for key, value in img_placeholders.items():
        html_out = re.sub(rf"<p>\s*{re.escape(key)}\s*</p>", lambda _: value, html_out)
        html_out = html_out.replace(key, value)
    for key, value in table_placeholders.items():
        safe_html = sanitize_table_html(value)
        html_out = re.sub(rf"<p>\s*{re.escape(key)}\s*</p>", lambda _: safe_html, html_out)
        html_out = html_out.replace(key, safe_html)
    if _HTML_TABLE_TOKEN_RE.search(html_out):
        html_out = _HTML_TABLE_TOKEN_RE.sub(
            '<div class="warning">Table placeholder could not be restored.</div>',
            html_out,
        )
    html_out = re.sub(r"&lt;sup&gt;([0-9]+)&lt;/sup&gt;", r"<sup>\1</sup>", html_out)
    html_out = re.sub(r"&lt;sub&gt;([0-9]+)&lt;/sub&gt;", r"<sub>\1</sub>", html_out)
    return html_out


def select_template_tag(
    paper: dict[str, Any], requested: str | None
) -> tuple[str | None, list[str]]:
    """Select template tag for paper rendering."""
    available = _available_templates(paper)
    if not available:
        return None, []
    default_tag = paper.get("default_template")
    if not default_tag:
        default_tag = "simple" if "simple" in available else available[0]
    selected = requested if requested in available else default_tag
    return selected, available


def render_paper_markdown(
    paper: dict[str, Any],
    fallback_language: str,
    *,
    template_tag: str | None = None,
) -> tuple[str, str, str | None]:
    """Render paper content using template and return markdown, template name, and optional warning."""
    selected_tag, _ = select_template_tag(paper, template_tag)
    selected_paper = paper
    if selected_tag:
        selected_paper = (paper.get("templates") or {}).get(selected_tag, paper)

    template_name = selected_tag or selected_paper.get("prompt_template")
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

    context = dict(selected_paper)
    if not context.get("output_language"):
        context["output_language"] = fallback_language
    if "publication_venue" in context:
        context["publication_venue"] = normalize_venue(str(context.get("publication_venue") or ""))
    return template.render(**context), str(template_name), warning
