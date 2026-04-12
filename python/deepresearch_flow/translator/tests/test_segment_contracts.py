from __future__ import annotations

import pytest

from deepresearch_flow.translator.segment import Node, Segment, reassemble_segments


def test_reassemble_segments_preserves_origin_text_for_untranslated_nodes() -> None:
    segments = [Segment(kind="nodes", content=[0, 1])]
    nodes = {
        0: Node(nid=0, origin_text="A", translated_text="ZH:A"),
        1: Node(nid=1, origin_text="B", translated_text=""),
    }

    assert reassemble_segments(segments, nodes) == "ZH:AB"


def test_reassemble_segments_rejects_missing_node_ids() -> None:
    segments = [Segment(kind="nodes", content=[0, 1])]
    nodes = {0: Node(nid=0, origin_text="A", translated_text="ZH:A")}

    with pytest.raises(ValueError, match="missing node during reassembly"):
        reassemble_segments(segments, nodes)
