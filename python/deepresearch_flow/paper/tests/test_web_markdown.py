from __future__ import annotations

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


def test_create_md_renderer_renders_links_and_tables() -> None:
    md = create_md_renderer()
    html_out = md.render(
        "\n".join(
            [
                "[example](https://example.com)",
                "",
                "| a | b |",
                "| --- | --- |",
                "| 1 | 2 |",
            ]
        )
    )
    assert '<a href="https://example.com"' in html_out
    assert "<table>" in html_out


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
    assert list(placeholders.values()) == ["$x+y$", "$$z$$"]
    assert "$x+y$" not in rendered
    assert "$$z$$" not in rendered
    assert "$ignore$" in rendered
    assert "`$also_ignore$`" in rendered

    escaped, escaped_placeholders = extract_math_placeholders(r"$$a\$$b$$")
    assert list(escaped_placeholders.values()) == ["$$a\\$$b$$"]
    assert escaped != r"$$a\$$b$$"


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
    assert list(img_placeholders.values()) == ['<img src="data:image/png;base64,AA==" alt="x" />']
    assert "<img src=" not in img_rendered

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
    assert list(table_placeholders.values()) == ["<table><tr><td>x</td></tr></table>"]
    assert "<table><tr><td>x</td></tr></table>" not in table_rendered

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


def test_select_template_tag_prefers_requested_template_or_default() -> None:
    paper = {
        "default_template": "simple",
        "templates": {"simple": {}, "deep_read": {}},
    }

    assert select_template_tag(paper, None) == ("simple", ["simple", "deep_read"])
    assert select_template_tag(paper, "deep_read") == ("deep_read", ["simple", "deep_read"])
    assert select_template_tag(paper, "missing") == ("simple", ["simple", "deep_read"])
    assert select_template_tag({}, None) == (None, [])


def test_render_paper_markdown_uses_requested_or_default_template() -> None:
    paper = {
        "paper_title": "Deep Paper",
        "paper_authors": [],
        "publication_venue": "{{NeurIPS}} 2024",
        "output_language": "zh",
        "templates": {
            "simple": {
                "paper_title": "Simple Paper",
                "paper_authors": ["Alice"],
                "publication_venue": "{{NeurIPS}} 2024",
            },
            "deep_read": {
                "paper_title": "Deep Paper",
                "paper_authors": ["Alice"],
                "publication_venue": "{{NeurIPS}} 2024",
            },
        },
    }

    rendered, template_name, warning = render_paper_markdown(paper, "zh", template_tag="deep_read")
    assert template_name == "deep_read"
    assert warning is None
    assert "Module A: Reading Alignment and Input Check" in rendered
    assert "**输出语言 / Output Language:** zh" in rendered
    assert "**期刊/会议 / Publication Venue:** NeurIPS 2024" in rendered

    fallback_paper = {
        "paper_title": "Paper",
        "paper_authors": ["Alice"],
        "prompt_template": "missing-template",
        "publication_venue": "{{ACL}}",
    }
    fallback_rendered, fallback_template_name, fallback_warning = render_paper_markdown(fallback_paper, "en")
    assert fallback_template_name == "default_paper"
    assert fallback_warning == "Rendered using default template (missing template)."
    assert "# Paper" in fallback_rendered
    assert "**Authors:** Alice" in fallback_rendered
    assert "**Publication Venue:** ACL" in fallback_rendered
