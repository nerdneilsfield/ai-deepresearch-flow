from pathlib import Path

from deepresearch_flow.paper.db_ops import enrich_with_bibtex
from deepresearch_flow.paper.extract import title_found_in_source

HEAD = "Check for updates\n\n# Hand gesture 3D pose estimation method based on swin transformer and CNN\n\nRong Dang"


def test_title_check_accepts_original_and_rejects_translation():
    assert title_found_in_source("Hand Gesture 3D Pose Estimation Method Based on Swin Transformer and CNN", HEAD)
    assert not title_found_in_source("基于Swin Transformer与CNN的手势三维姿态估计方法", HEAD)
    assert title_found_in_source("HandR$^{2}$N$^{2}$: Iterative", "# HandR²N²: Iterative 3D")


def test_bibtex_matched_by_pdf_filename_overrides_title_and_authors(tmp_path: Path):
    bib = tmp_path / "x.bib"
    bib.write_text(
        "@misc{k1,\n  title = {Humanoid {{Policy}} \\textasciitilde{} {{Human Policy}}},\n"
        "  author = {Qiu, Ri-Zhao and Yang, Shiqi},\n"
        "  file = {/z/Qiu et al. - 2025 - Humanoid Policy Human Policy.pdf}\n}\n",
        encoding="utf-8",
    )
    paper = {
        "paper_title": "人形策略",
        "paper_authors": ["Shiqi Yang", "Ri-Zhao Qiu"],
        "source_path": "/md/Qiu_et_al._-_2025_-_Humanoid_Policy_Human_Policy.md",
    }
    enrich_with_bibtex([paper], bib)
    assert paper["bibtex"]["key"] == "k1"
    assert paper["paper_title"] == "Humanoid Policy ~ Human Policy"
    assert paper["paper_authors"] == ["Ri-Zhao Qiu", "Shiqi Yang"]
