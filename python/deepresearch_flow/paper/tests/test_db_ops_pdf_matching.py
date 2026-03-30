from __future__ import annotations

from pathlib import Path

from deepresearch_flow.paper import db_ops


def _build_pdf_index(pdf_dir: Path) -> dict[str, list[Path]]:
    return db_ops._build_file_index([pdf_dir], suffixes={".pdf"})


def test_resolve_pdf_matches_author_title_filename_without_year(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / (
        "Liu - Event-driven asynchronous graph neural network FPGA accelerator "
        "for real-time edge vision.pdf"
    )
    pdf_path.write_text("dummy")

    paper = {
        "paper_title": "Event-Driven Asynchronous Graph Neural Network FPGA Accelerator for Real-time Edge Vision",
        "source_path": str(
            tmp_path / "Liu_-_Event-driven_asynchronous_graph_neural_network_FPGA_accelerator_for_real-time_edge_vision.md"
        ),
    }

    resolved = db_ops._resolve_pdf(paper, _build_pdf_index(pdf_dir))

    assert resolved == pdf_path.resolve()


def test_resolve_pdf_matches_title_with_multiplication_symbol_variant(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / (
        "Yousefzadeh et al. - 2015 - Fast pipeline 128×128 pixel spiking convolution core "
        "for event-driven vision processing in FPGAs.pdf"
    )
    pdf_path.write_text("dummy")

    paper = {
        "paper_title": "Fast Pipeline 128x128 Pixel Spiking Convolution Core for Event-Driven Vision Processing in FPGAs",
        "source_path": str(
            tmp_path
            / "Yousefzadeh_et_al._-_2015_-_Fast_pipeline_128_128_pixel_spiking_convolution_core_for_event-driven_vision_processing_in_FPGAs.md"
        ),
    }

    resolved = db_ops._resolve_pdf(paper, _build_pdf_index(pdf_dir))

    assert resolved == pdf_path.resolve()


def test_resolve_pdf_only_logs_when_all_fallbacks_fail(tmp_path: Path, capsys) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / (
        "Barranco et al. - 2018 - Real-time clustering and multi-target tracking "
        "using event-based sensors.pdf"
    )
    pdf_path.write_text("dummy")

    paper = {
        "paper_title": "Real-time clustering and multi-target tracking using event-based sensors",
        "source_path": str(
            tmp_path
            / "Barranco_et_al._-_2018_-_Real-time_clustering_and_multi-target_tracking_using_event-based_sensors.md"
        ),
    }

    resolved = db_ops._resolve_pdf(paper, _build_pdf_index(pdf_dir))
    captured = capsys.readouterr()

    assert resolved == pdf_path.resolve()
    assert captured.err == ""


def test_resolve_pdf_falls_back_to_source_path_title_when_paper_title_is_non_latin(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / (
        "Liu et al. - 2026 - Fine-grained data integration for high throughput and "
        "bandwidth-efficient computation on FPGAs.pdf"
    )
    pdf_path.write_text("dummy")

    paper = {
        "paper_title": "面向FPGA高吞吐量与带宽高效计算的细粒度数据集成",
        "source_path": str(
            tmp_path
            / "Liu_et_al._-_2026_-_Fine-grained_data_integration_for_high_throughput_and_bandwidth-efficient_computation_on_FPGAs.md"
        ),
    }

    resolved = db_ops._resolve_pdf(paper, _build_pdf_index(pdf_dir))

    assert resolved == pdf_path.resolve()
