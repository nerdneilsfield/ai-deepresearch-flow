from __future__ import annotations

from deepresearch_flow.paper.web import templates


def test_render_template_includes_context_and_shared_layout_bits() -> None:
    html = templates.render_template(
        "detail.html",
        title="Paper",
        header_title="Paper Title",
        current_view="summary",
        body_html="<p>Hello</p>",
        summary_template_name="simple",
    )

    assert "<title>Paper</title>" in html
    assert "Paper Title" in html
    assert "Hello" in html
    assert "Template: simple" in html
    assert templates.REPO_URL in html
    assert "GitHub" in html
    assert "header-version" in html


def test_render_template_keeps_default_page_metadata_and_shared_assets() -> None:
    html = templates.render_template("detail.html", body_html="")

    assert '<html lang="en">' in html
    assert "<title></title>" in html
    assert '<meta name="robots" content="noindex, nofollow, noarchive, nosnippet"' in html
    assert "/static/css/main.css" in html
    assert "katex.min.css" in html
    assert "header-version" in html


def test_build_pdfjs_viewer_url_handles_optional_cdn() -> None:
    assert (
        templates.build_pdfjs_viewer_url("/pdf/file.pdf?download=1#frag")
        == "/pdfjs/web/viewer.html?file=%2Fpdf%2Ffile.pdf%3Fdownload%3D1%23frag&allow_origin=1"
    )
    assert (
        templates.build_pdfjs_viewer_url("/pdf/file.pdf", cdn_base_url="https://cdn.example.com/")
        == "/pdfjs/web/viewer.html?file=%2Fpdf%2Ffile.pdf&allow_origin=1&cdn=https%3A%2F%2Fcdn.example.com"
    )
