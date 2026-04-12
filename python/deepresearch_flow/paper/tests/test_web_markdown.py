from __future__ import annotations

from types import SimpleNamespace

from deepresearch_flow.paper.web.markdown import (
    create_md_renderer,
    extract_html_img_placeholders,
    extract_html_table_placeholders,
    extract_math_placeholders,
    normalize_fenced_code_blocks,
    normalize_footnote_definitions,
    normalize_markdown_images,
    normalize_mermaid_blocks,
    normalize_unbalanced_fences,
    render_markdown_with_math_placeholders,
    render_paper_markdown,
    sanitize_img_html,
    sanitize_table_html,
    select_template_tag,
    strip_paragraph_wrapped_tables,
)


def test_create_md_renderer_enables_expected_features() -> None:
    md = create_md_renderer()
    assert md.options["linkify"] is True
    assert md.options["html"] is False


def test_strip_paragraph_wrapped_tables() -> None:
    text = "<p>| a | b |\n| c | d |</p>"
    assert strip_paragraph_wrapped_tables(text) == "| a | b |\n| c | d |"


def test_normalize_footnote_definitions_handles_headings_lists_and_fences() -> None:
    text = "\n".join(
        [
            "Intro [1]",
            "## Notes",
            "1. First note",
            "  continued",
            "",
            "```md",
            "2. keep fenced",
            "```",
            "[^3] explicit footnote",
            "### Next",
            "Done [2]",
        ]
    )
    normalized = normalize_footnote_definitions(text)
    assert "Intro [^1]" in normalized
    assert "[^1]: First note continued" in normalized
    assert "2. keep fenced" in normalized
    assert "[^3]: explicit footnote" in normalized
    assert "Done [^2]" in normalized


def test_normalize_markdown_images_respects_lists_tables_and_fences() -> None:
    text = "\n".join(
        [
            "Prefix ![img](a.png)",
            "![solo](b.png)",
            "- ![list](c.png)",
            "| ![table](d.png) |",
            "```",
            "![code](e.png)",
            "```",
        ]
    )
    normalized = normalize_markdown_images(text)
    assert "Prefix\n\n![img](a.png)" in normalized
    assert "\n\n![solo](b.png)" in normalized
    assert "- ![list](c.png)" in normalized
    assert "| ![table](d.png) |" in normalized
    assert "![code](e.png)" in normalized


def test_normalize_fenced_code_blocks_and_mermaid_blocks() -> None:
    text = "Before ```python\ncode"
    assert normalize_fenced_code_blocks(text) == "Before\n```python\ncode"

    mermaid = "\n".join(
        [
            "```mermaid",
            "graph TD",
            "Legend: blue means yes",
            "节点定位: test",
            "A-->B",
            "```",
        ]
    )
    normalized = normalize_mermaid_blocks(mermaid)
    assert "graph TD" in normalized
    assert "A-->B" in normalized
    assert normalized.endswith("Legend: blue means yes\n节点定位: test")

    unclosed = normalize_mermaid_blocks("```mermaid\ngraph TD\nLegend: x")
    assert unclosed.endswith("graph TD\nLegend: x")


def test_normalize_unbalanced_fences_handles_empty_and_reopened_blocks() -> None:
    assert normalize_unbalanced_fences("```python\ncontent") == "content"
    assert normalize_unbalanced_fences("```\n```") == ""
    reopened = normalize_unbalanced_fences("```python\n```\n```json\nbody")
    assert reopened == "body"


def test_extract_math_placeholders_skips_fences_and_inline_code() -> None:
    text = "\n".join(
        [
            "Inline $x+y$ and block $$z$$.",
            "```",
            "$ignore$",
            "```",
            "`$also_ignore$`",
        ]
    )
    rendered, placeholders = extract_math_placeholders(text)
    assert "@@MATH_0@@" in rendered
    assert "@@MATH_1@@" in rendered
    assert placeholders["@@MATH_0@@"] == "$x+y$"
    assert placeholders["@@MATH_1@@"] == "$$z$$"
    assert "$ignore$" in rendered
    assert "`$also_ignore$`" in rendered

    escaped, escaped_placeholders = extract_math_placeholders(r"$$a\$$b$$")
    assert escaped_placeholders["@@MATH_0@@"] == "$$a\\$$b$$"


def test_sanitize_table_html_and_images() -> None:
    safe = sanitize_table_html('<table><tr><td colspan="2" align="center">ok</td></tr><script>x</script></table>')
    assert "<table>" in safe
    assert 'colspan="2"' in safe
    assert "<script>" not in safe
    assert sanitize_table_html("<table><tr><td>&amp;&#169;</td></tr></table>") == "<table><tr><td>&amp;©</td></tr></table>"

    assert sanitize_table_html("<table") == "<pre><code>&lt;table</code></pre>"
    assert sanitize_img_html('<img src="data:image/png;base64,AA==" alt="x">') == '<img src="data:image/png;base64,AA==" alt="x" />'
    assert sanitize_img_html('<img src=data:image/png;base64,AA==>') == '<img src="data:image/png;base64,AA==" />'
    assert sanitize_img_html('<img src="/static/x.png">') is None


def test_extract_html_img_and_table_placeholders() -> None:
    img_text = 'Before <img src="data:image/png;base64,AA==" alt="x"> after'
    img_rendered, img_placeholders = extract_html_img_placeholders(img_text)
    assert "@@HTML_IMG_0@@" in img_rendered
    assert img_placeholders["@@HTML_IMG_0@@"] == '<img src="data:image/png;base64,AA==" alt="x" />'

    img_fenced = "```\n<img src=\"data:image/png;base64,AA==\">\n```"
    same_img, same_placeholders = extract_html_img_placeholders(img_fenced)
    assert same_img == img_fenced
    assert same_placeholders == {}

    img_inline = "`<img src=\"data:image/png;base64,AA==\">`"
    same_inline, inline_placeholders = extract_html_img_placeholders(img_inline)
    assert same_inline == img_inline
    assert inline_placeholders == {}

    table_text = "A\n<table><tr><td>x</td></tr></table>\nB"
    table_rendered, table_placeholders = extract_html_table_placeholders(table_text)
    assert "@@HTML_TABLE_0@@" in table_rendered
    assert table_placeholders["@@HTML_TABLE_0@@"] == "<table><tr><td>x</td></tr></table>"

    table_fenced = "```\n<table><tr><td>x</td></tr></table>\n```"
    same_table, same_table_placeholders = extract_html_table_placeholders(table_fenced)
    assert same_table == table_fenced
    assert same_table_placeholders == {}

    table_inline = "`<table><tr><td>x</td></tr></table>`"
    same_inline_table, inline_table_placeholders = extract_html_table_placeholders(table_inline)
    assert same_inline_table == table_inline
    assert inline_table_placeholders == {}


def test_render_markdown_with_math_placeholders_restores_special_content() -> None:
    md = create_md_renderer()
    text = "\n".join(
        [
            "Math $x$",
            '<img src="data:image/png;base64,AA==" alt="plot">',
            "<table><tr><td>1</td></tr></table>",
            "<sup>2</sup> and <sub>3</sub>",
        ]
    )
    html_out = render_markdown_with_math_placeholders(md, text)
    assert "$x$" in html_out
    assert '<img src="data:image/png;base64,AA==" alt="plot" />' in html_out
    assert "<table><tr><td>1</td></tr></table>" in html_out
    assert "<sup>2</sup>" in html_out
    assert "<sub>3</sub>" in html_out

    class _FakeMd:
        def render(self, text: str) -> str:
            return "<p>@@HTML_TABLE_99@@</p>"

    warning_html = render_markdown_with_math_placeholders(_FakeMd(), "plain")
    assert "Table placeholder could not be restored." in warning_html


def test_select_template_tag_and_render_paper_markdown(monkeypatch) -> None:
    paper = {
        "default_template": "simple",
        "prompt_template": "deep_read",
        "publication_venue": "{{NeurIPS}} 2024",
        "templates": {"simple": {"title": "Simple"}, "deep_read": {"title": "Deep"}},
    }

    monkeypatch.setattr("deepresearch_flow.paper.web.markdown._available_templates", lambda _: ["simple", "deep_read"])
    assert select_template_tag(paper, None) == ("simple", ["simple", "deep_read"])
    assert select_template_tag(paper, "deep_read") == ("deep_read", ["simple", "deep_read"])
    monkeypatch.setattr("deepresearch_flow.paper.web.markdown._available_templates", lambda _: [])
    assert select_template_tag(paper, None) == (None, [])
    monkeypatch.setattr("deepresearch_flow.paper.web.markdown._available_templates", lambda _: ["deep_read"])
    assert select_template_tag({}, None) == ("deep_read", ["deep_read"])

    monkeypatch.setattr("deepresearch_flow.paper.web.markdown._available_templates", lambda _: ["simple", "deep_read"])

    class _Template:
        def __init__(self, name: str):
            self.name = name

        def render(self, **context):
            return f"{self.name}|{context['output_language']}|{context.get('publication_venue','')}"

    monkeypatch.setattr("deepresearch_flow.paper.web.markdown.load_render_template", lambda name: _Template(str(name)))
    monkeypatch.setattr("deepresearch_flow.paper.web.markdown.load_default_template", lambda: _Template("default"))

    rendered, template_name, warning = render_paper_markdown(paper, "zh", template_tag="deep_read")
    assert rendered == "deep_read|zh|"
    assert template_name == "deep_read"
    assert warning is None

    def boom_load(name: str):
        raise RuntimeError("missing")

    monkeypatch.setattr("deepresearch_flow.paper.web.markdown.load_render_template", boom_load)
    rendered, template_name, warning = render_paper_markdown(paper, "en", template_tag="deep_read")
    assert rendered == "default|en|"
    assert template_name == "default_paper"
    assert warning == "Rendered using default template (missing template)."

    monkeypatch.setattr("deepresearch_flow.paper.web.markdown._available_templates", lambda _: [])
    bare_paper = {"publication_venue": "{{ACL}}"}
    rendered, template_name, warning = render_paper_markdown(bare_paper, "en")
    assert rendered == "default|en|ACL"
    assert template_name == "default_paper"
    assert warning == "Rendered using default template (no template specified)."
