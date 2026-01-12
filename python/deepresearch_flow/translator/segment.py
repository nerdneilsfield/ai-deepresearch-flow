"""Segment markdown into translatable nodes while preserving separators."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass
class Node:
    nid: int
    origin_text: str
    translated_text: str = ""


@dataclass
class Segment:
    kind: str  # "sep" or "nodes"
    content: str | list[int]


def _is_blank(line: str) -> bool:
    return len(line.strip()) == 0


def _looks_like_heading(line: str) -> bool:
    return re.match(r"^\s{0,3}#{1,6}\s+", line) is not None


def _looks_like_list_item(line: str) -> bool:
    return re.match(r"^\s{0,3}(-|\*|\+|\d{1,9}\.)\s+", line) is not None


def _split_long_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    tokens = re.split(r"(?<=[。！？!?\.])(\s+)", text)
    parts: list[str] = []
    buf = ""
    for token in tokens:
        if token == "":
            continue
        if buf and len(buf) + len(token) > max_chars:
            parts.append(buf)
            buf = ""
        buf += token
    if buf:
        parts.append(buf)
    final_parts: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            final_parts.append(part)
            continue
        soft_parts = _soft_split_long_sentence(part, max_chars)
        if len(soft_parts) == 1:
            final_parts.append(part)
        else:
            final_parts.extend(soft_parts)
    return final_parts


def _soft_split_long_sentence(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    tokens = re.split(r"(?<=[,，、;；:：])(\s+)", text)
    parts: list[str] = []
    buf = ""
    for token in tokens:
        if token == "":
            continue
        if buf and len(buf) + len(token) > max_chars:
            parts.append(buf)
            buf = ""
        buf += token
    if buf:
        parts.append(buf)
    if len(parts) <= 1:
        return [text]
    return parts


def _collect_list_block(lines: list[str], start: int) -> tuple[list[str], int]:
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = [lines[start]]
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if _is_blank(line):
            break
        if _looks_like_list_item(line) and (
            len(line) - len(line.lstrip())
        ) <= indent:
            break
        if len(line) - len(line.lstrip()) > indent:
            block.append(line)
            i += 1
            continue
        break
    return block, i


def split_to_segments(
    text: str, max_chunk_chars: int
) -> tuple[list[Segment], dict[int, Node]]:
    lines = text.splitlines(keepends=True)
    segments: list[Segment] = []
    nodes: dict[int, Node] = {}
    node_id = 0
    buffer: list[str] = []

    def flush_buffer() -> None:
        nonlocal node_id
        if not buffer:
            return
        block_text = "".join(buffer)
        buffer.clear()
        parts = _split_long_text(block_text, max_chunk_chars)
        node_ids: list[int] = []
        for part in parts:
            if part == "":
                continue
            nodes[node_id] = Node(nid=node_id, origin_text=part)
            node_ids.append(node_id)
            node_id += 1
        if node_ids:
            segments.append(Segment(kind="nodes", content=node_ids))

    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_blank(line):
            flush_buffer()
            segments.append(Segment(kind="sep", content=line))
            i += 1
            continue
        if _looks_like_heading(line):
            flush_buffer()
            nodes[node_id] = Node(nid=node_id, origin_text=line)
            segments.append(Segment(kind="nodes", content=[node_id]))
            node_id += 1
            i += 1
            continue
        if _looks_like_list_item(line):
            flush_buffer()
            block, next_idx = _collect_list_block(lines, i)
            block_text = "".join(block)
            parts = _split_long_text(block_text, max_chunk_chars)
            node_ids: list[int] = []
            for part in parts:
                if part == "":
                    continue
                nodes[node_id] = Node(nid=node_id, origin_text=part)
                node_ids.append(node_id)
                node_id += 1
            if node_ids:
                segments.append(Segment(kind="nodes", content=node_ids))
            i = next_idx
            continue
        buffer.append(line)
        i += 1

    flush_buffer()
    return segments, nodes


def reassemble_segments(segments: Iterable[Segment], nodes: dict[int, Node]) -> str:
    parts: list[str] = []
    for segment in segments:
        if segment.kind == "sep":
            parts.append(str(segment.content))
            continue
        node_ids = segment.content
        if not isinstance(node_ids, list):
            continue
        for node_id in node_ids:
            node = nodes.get(node_id)
            if node is not None:
                parts.append(node.translated_text)
    return "".join(parts)
