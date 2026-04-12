from __future__ import annotations

from deepresearch_flow.paper.chunker import (
    SearchableField,
    chunk_fields,
    extract_searchable_fields,
)


def test_extract_searchable_fields_prefers_paper_title_over_title() -> None:
    record = {
        "paper_title": "Paper Title",
        "title": "Fallback Title",
        "summary": "A short summary.",
    }

    fields = extract_searchable_fields(record, "simple")

    titles = [field for field in fields if field.chunk_type == "title"]
    assert len(titles) == 1
    assert titles[0].text == "Paper Title"
    assert titles[0].template_tag == ""


def test_extract_searchable_fields_handles_simple_phi() -> None:
    record = {
        "paper_title": "Attention Is All You Need",
        "summary": "This paper introduces the Transformer architecture.",
        "paper_authors": ["Vaswani", "Shazeer"],
    }

    fields = extract_searchable_fields(record, "simple_phi")

    assert [field.chunk_type for field in fields] == ["title", "abstract"]
    assert fields[0].text == "Attention Is All You Need"
    assert fields[1].text == "This paper introduces the Transformer architecture."
    assert fields[1].template_tag == "simple_phi"


def test_extract_searchable_fields_fallback_scans_string_fields() -> None:
    record = {
        "title": "Fallback Title",
        "custom_field": "Some text content",
        "number_field": 42,
    }

    fields = extract_searchable_fields(record, "unknown_template")

    assert [field.chunk_type for field in fields] == ["title", "content"]
    assert fields[0].text == "Fallback Title"
    assert fields[1].text == "Some text content"


def test_chunk_fields_keeps_title_and_qa_unsplit() -> None:
    fields = [
        SearchableField(
            field_name="title",
            chunk_type="title",
            text="Short title",
            template_tag="",
            lang="",
        ),
        SearchableField(
            field_name="simple/qa[0]",
            chunk_type="qa",
            text="Q: " + "question " * 200 + "\nA: " + "answer " * 200,
            template_tag="simple",
            lang="",
        ),
    ]

    chunks = chunk_fields(fields, max_tokens=16, overlap_tokens=4)

    assert len(chunks) == 2
    assert [chunk.chunk_type for chunk in chunks] == ["title", "qa"]
    assert [chunk.chunk_index for chunk in chunks] == [0, 0]
    assert chunks[0].text == "Short title"


def test_chunk_fields_uses_sliding_window_for_long_content() -> None:
    field = SearchableField(
        field_name="deep_read/findings",
        chunk_type="content",
        text=("word " * 200).strip(),
        template_tag="deep_read",
        lang="",
    )

    chunks = chunk_fields([field], max_tokens=20, overlap_tokens=5)

    assert len(chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.field_name == "deep_read/findings" for chunk in chunks)
    assert all(chunk.template_tag == "deep_read" for chunk in chunks)



def test_chunk_fields_keeps_complete_paragraphs_together() -> None:
    para_a = ("alpha " * 80).strip()
    para_b = ("beta " * 80).strip()
    text = f"{para_a}\n\n{para_b}"
    field = SearchableField(
        field_name="deep_read/findings",
        chunk_type="content",
        text=text,
        template_tag="deep_read",
        lang="",
    )

    chunks = chunk_fields([field], max_tokens=120, overlap_tokens=10)

    assert [chunk.text for chunk in chunks] == [para_a, para_b]
    assert all("\n\n" not in chunk.text for chunk in chunks)



def test_chunk_fields_does_not_overlap_across_paragraph_chunks() -> None:
    para_a = ("alpha " * 60).strip()
    para_b = ("beta " * 60).strip()
    para_c = ("gamma " * 60).strip()
    text = f"{para_a}\n\n{para_b}\n\n{para_c}"
    field = SearchableField(
        field_name="deep_read/findings",
        chunk_type="content",
        text=text,
        template_tag="deep_read",
        lang="",
    )

    chunks = chunk_fields([field], max_tokens=120, overlap_tokens=10)

    assert len(chunks) == 2
    assert chunks[0].text == f"{para_a}\n\n{para_b}"
    assert chunks[1].text == para_c
