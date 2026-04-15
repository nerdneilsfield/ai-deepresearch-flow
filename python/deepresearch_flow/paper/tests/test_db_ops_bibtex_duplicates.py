from __future__ import annotations

from pathlib import Path

from deepresearch_flow.paper import db_ops


def test_enrich_with_bibtex_tolerates_duplicate_entry_keys(tmp_path: Path) -> None:
    bib_path = tmp_path / "library.bib"
    bib_path.write_text(
        "@article{dupkey,\n"
        "  title={Graph Matching with Semantic Cues},\n"
        "  year={2020}\n"
        "}\n"
        "@article{dupkey,\n"
        "  title={Graph Matching with Semantic Cues},\n"
        "  year={2020}\n"
        "}\n",
        encoding="utf-8",
    )

    papers = [{"paper_title": "Graph Matching with Semantic Cues"}]

    db_ops.enrich_with_bibtex(papers, bib_path)

    assert isinstance(papers[0].get("bibtex"), dict)
    assert papers[0]["bibtex"]["key"] == "dupkey"
    assert papers[0]["bibtex"]["fields"]["title"] == "Graph Matching with Semantic Cues"
