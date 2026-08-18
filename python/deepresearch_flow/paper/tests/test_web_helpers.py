from __future__ import annotations

from dataclasses import dataclass

from starlette.datastructures import QueryParams
from starlette.requests import Request

from deepresearch_flow.paper.web.filters import (
    compute_counts,
    matches_presence,
    merge_filter_set,
    normalize_presence_value,
    normalize_sort_value,
    parse_filter_query,
    parse_filters,
    presence_filter,
    safe_int,
    sorted_ids,
    template_tag_map,
    tokenize_filter_query,
)
from deepresearch_flow.paper.web.query import Query, QueryTerm, parse_query
from deepresearch_flow.paper.web.text import (
    extract_summary_snippet,
    normalize_summary_text,
    normalize_title,
    normalize_venue,
)


@dataclass
class _DummyIndex:
    template_tags: list[str]
    papers: dict[int, dict]
    ordered_ids: list[int]
    md_path_by_hash: dict[str, str]
    pdf_path_by_hash: dict[str, str]
    translated_md_by_hash: dict[str, dict]


def _request(query_string: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": query_string.encode("utf-8"),
    }
    return Request(scope)


def test_filter_tokenization_and_presence_helpers() -> None:
    assert tokenize_filter_query('pdf:with template:"deep read" has:source') == [
        "pdf:with",
        "template:deep read",
        "has:source",
    ]
    assert normalize_presence_value("YES") == "with"
    assert normalize_presence_value("without") == "without"
    assert normalize_presence_value("maybe") is None

    assert presence_filter(["with", "0"]) is None
    assert presence_filter(["with"]) == {"with"}
    assert presence_filter(["with", "without"]) is None
    assert merge_filter_set({"with"}, {"with", "without"}) == {"with"}
    assert merge_filter_set(None, {"without"}) == {"without"}
    assert matches_presence({"with"}, True) is True
    assert matches_presence({"with"}, False) is False
    assert matches_presence({"without"}, False) is True


def test_parse_filter_query_and_request_filters() -> None:
    parsed = parse_filter_query(
        "tmpl:simple,deep_read pdf:yes no:translated has:summary source:without"
    )
    assert parsed["template"] == {"simple", "deep_read"}
    assert parsed["pdf"] == {"with"}
    assert parsed["translated"] == {"without"}
    assert parsed["summary"] == {"with"}
    assert parsed["source"] == {"without"}
    malformed = parse_filter_query("pdf: template: has:unknown no: weird summary:maybe")
    assert malformed == {
        "pdf": set(),
        "source": set(),
        "summary": set(),
        "translated": set(),
        "template": set(),
    }

    request = _request(
        "page=0&page_size=999&q= attention &fq=pdf:with&pdf=yes&source=no&summary=with"
        "&translated=without&template=simple&sort_by=year&sort_dir=weird"
    )
    filters = parse_filters(request)
    assert filters == {
        "page": 1,
        "page_size": 200,
        "q": "attention",
        "filter_query": "pdf:with",
        "pdf": ["yes"],
        "source": ["no"],
        "summary": ["with"],
        "translated": ["without"],
        "template": ["simple"],
        "sort_by": "year",
        "sort_dir": "desc",
    }


def test_compute_counts_template_map_and_sorted_ids() -> None:
    index = _DummyIndex(
        template_tags=["simple", "deep_read"],
        ordered_ids=[2, 1, 3],
        md_path_by_hash={"hash-1": "a.md"},
        pdf_path_by_hash={"hash-1": "a.pdf"},
        translated_md_by_hash={"hash-1": {"zh": "a-zh.md"}},
        papers={
            1: {
                "_template_tags_lc": ["simple"],
                "_has_summary": True,
                "source_hash": "hash-1",
                "_year": "2024",
                "_month": "03",
                "paper_title": "Alpha",
                "_venue": "ACL",
                "_authors": ["Alice"],
            },
            2: {
                "_template_tags_lc": ["deep_read"],
                "_has_summary": False,
                "source_hash": "hash-2",
                "_year": "2023",
                "_month": "10",
                "paper_title": "Beta",
                "_venue": "NeurIPS",
                "_authors": ["Bob"],
            },
            3: {
                "_is_pdf_only": True,
                "_template_tags_lc": ["simple"],
                "_has_summary": True,
                "source_hash": "hash-3",
                "_year": "2025",
                "_month": "01",
                "paper_title": "Gamma",
                "_venue": "ICLR",
                "_authors": ["Carol"],
            },
        },
    )

    assert template_tag_map(index) == {"simple": "simple", "deep_read": "deep_read"}
    counts = compute_counts(index, {1, 2, 3})
    assert counts["total"] == 2
    assert counts["pdf"] == 1
    assert counts["source"] == 1
    assert counts["summary"] == 1
    assert counts["translated"] == 1
    assert counts["templates"] == {"simple": 1, "deep_read": 1}

    assert safe_int("12") == 12
    assert safe_int("bad") == 0
    assert normalize_sort_value(None) == ""
    assert normalize_sort_value("  AbC ") == "abc"

    assert sorted_ids(index, {1, 2}, "", "desc") == [2, 1]
    assert sorted_ids(index, {1, 2}, "title", "asc") == [1, 2]
    assert sorted_ids(index, {1, 2}, "year", "desc") == [1, 2]
    assert sorted_ids(index, {1, 2}, "venue", "asc") == [1, 2]
    assert sorted_ids(index, {1, 2}, "author", "desc") == [2, 1]
    assert sorted_ids(index, {1, 2}, "unknown", "asc") == [1, 2]


def test_query_parser_handles_or_fields_and_negation() -> None:
    query = parse_query('title:"Graph Nets" -venue:workshop OR tag:vision plain')
    assert query == Query(
        groups=[
            [
                QueryTerm(field="title", value="Graph Nets", negated=False),
                QueryTerm(field="venue", value="workshop", negated=True),
            ],
            [
                QueryTerm(field="tag", value="vision", negated=False),
                QueryTerm(field=None, value="plain", negated=False),
            ],
        ]
    )
    assert parse_query("") == Query(groups=[[]])
    assert parse_query("OR OR") == Query(groups=[[]])
    assert parse_query("-   ") == Query(groups=[[]])


def test_web_text_helpers_normalize_and_extract_snippets() -> None:
    title = normalize_title(
        "A <inline-formula><tex-math>x^2</tex-math></inline-formula> &amp; <b>Title</b>"
    )
    assert title == "A x^2 & Title"
    assert normalize_title("") == ""
    assert normalize_title("<inline-formula>ignored</inline-formula>") == ""

    assert normalize_venue("{{NeurIPS}} 2024") == "NeurIPS 2024"
    assert normalize_venue("") == ""
    assert (
        normalize_summary_text(r"First\nSecond<p>Third</p><p>Fourth</p>")
        == "First\nSecond\n\nThird\n\nFourth"
    )

    paper = {
        "templates": {
            "deep_read": {"summary": "ignored"},
            "simple": {"abstract": "<b>Hello</b> &amp; world"},
        }
    }
    assert extract_summary_snippet(paper, max_len=100) == "Hello & world"
    assert (
        extract_summary_snippet({"summary": r"First\nSecond<p>Third</p><p>Fourth</p>"})
        == "First Second Third Fourth"
    )

    long_paper = {"summary": "x" * 20}
    assert extract_summary_snippet(long_paper, max_len=10) == ("x" * 9) + "…"
    assert extract_summary_snippet({}, max_len=10) == ""
    assert extract_summary_snippet({"templates": {"simple": "bad"}}, max_len=10) == ""
