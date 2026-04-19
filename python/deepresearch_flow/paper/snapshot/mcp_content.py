from __future__ import annotations

import json
import re
from typing import Any

DEFAULT_MAX_CHARS = 8_000
_FORBIDDEN_KEY_CHARS = {".", "[", "]"}


class SummaryContentError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.message = message
        self.details = dict(details)
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.details}


class MarkdownContentError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.message = message
        self.details = dict(details)
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.details}


def truncate_text(text: str, max_chars: int | None) -> str:
    """Return text truncated to the requested maximum length.

    This helper is used by summary previews and keyed summary reads.
    Legacy full-text MCP responses preserve their historical truncation marker
    in ``mcp_server._truncate`` instead of calling this helper directly.
    """
    if max_chars is None or max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars]


def _require_positive_max_chars(max_chars: int | None, *, error_cls) -> int | None:
    if max_chars is None:
        return None
    if max_chars <= 0:
        raise error_cls(
            "invalid_max_chars",
            "max_chars must be a positive integer",
            max_chars=max_chars,
        )
    return max_chars


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _parse_summary_json(summary_json: str) -> Any:
    try:
        return json.loads(summary_json)
    except json.JSONDecodeError as exc:
        raise SummaryContentError(
            "invalid_summary_json",
            "Summary JSON is invalid",
            detail=str(exc),
        ) from exc


def _validate_field_name(name: str, *, path: str) -> None:
    if not name or any(ch in name for ch in _FORBIDDEN_KEY_CHARS):
        raise SummaryContentError(
            "invalid_summary_key",
            "Summary field names cannot contain '.', '[' or ']'",
            key=path,
            field_name=name,
        )


def _join_object_path(parent: str, field_name: str) -> str:
    return f"{parent}.{field_name}" if parent else field_name


def _join_array_path(parent: str, index: int) -> str:
    return f"{parent}[{index}]" if parent else f"[{index}]"


def _parse_summary_key(key: str) -> list[tuple[str, int | str]]:
    if key is None:
        raise SummaryContentError("invalid_summary_key", "Summary key cannot be empty")
    text = key.strip()
    if not text:
        raise SummaryContentError("invalid_summary_key", "Summary key cannot be empty")

    segments: list[tuple[str, int | str]] = []
    i = 0
    length = len(text)
    expecting_segment = True
    while i < length:
        ch = text[i]
        if ch == ".":
            raise SummaryContentError("invalid_summary_key", "Summary key has an empty field name", key=key)
        if ch == "[":
            end = text.find("]", i + 1)
            if end == -1:
                raise SummaryContentError("invalid_summary_key", "Summary key has an unterminated array index", key=key)
            index_text = text[i + 1 : end]
            if not index_text.isdigit():
                raise SummaryContentError("invalid_summary_key", "Summary key array indexes must be non-negative integers", key=key)
            segments.append(("index", int(index_text)))
            i = end + 1
            expecting_segment = False
            if i < length:
                if text[i] == ".":
                    i += 1
                    if i >= length:
                        raise SummaryContentError("invalid_summary_key", "Summary key has an empty field name", key=key)
                    expecting_segment = True
                elif text[i] == "[":
                    continue
                else:
                    raise SummaryContentError(
                        "invalid_summary_key",
                        "Summary key must separate path segments with '.'",
                        key=key,
                    )
            continue

        start = i
        while i < length and text[i] not in ".[":
            i += 1
        name = text[start:i]
        if not name:
            raise SummaryContentError("invalid_summary_key", "Summary key has an empty field name", key=key)
        if any(ch in name for ch in _FORBIDDEN_KEY_CHARS):
            raise SummaryContentError(
                "invalid_summary_key",
                "Summary field names cannot contain '.', '[' or ']'",
                key=key,
                field_name=name,
            )
        segments.append(("field", name))
        expecting_segment = False
        if i < length:
            if text[i] == ".":
                i += 1
                if i >= length:
                    raise SummaryContentError("invalid_summary_key", "Summary key has an empty field name", key=key)
                expecting_segment = True
            elif text[i] == "[":
                continue
            else:
                raise SummaryContentError("invalid_summary_key", "Summary key contains invalid syntax", key=key)

    if expecting_segment and not segments:
        raise SummaryContentError("invalid_summary_key", "Summary key cannot be empty")
    return segments


def _serialize_value(value: Any) -> tuple[str, str]:
    value_type = _json_type(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")), "application/json"
    if isinstance(value, str):
        return value, "text/plain"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")), "text/plain"


def get_summary_keys(
    summary_json: str,
    max_depth: int = 2,
    include_preview: bool = False,
) -> dict[str, Any]:
    """Return recursively discovered summary key paths in source order."""
    root = _parse_summary_json(summary_json)
    depth_limit = max(0, int(max_depth))
    paths: list[dict[str, Any]] = []

    def walk(value: Any, path: str, depth: int) -> None:
        if isinstance(value, dict):
            for field_name, child in value.items():
                _validate_field_name(str(field_name), path=_join_object_path(path, str(field_name)))
                child_path = _join_object_path(path, str(field_name))
                entry: dict[str, Any] = {"key": child_path, "type": _json_type(child)}
                if isinstance(child, list):
                    entry["length"] = len(child)
                if include_preview and isinstance(child, str):
                    entry["preview"] = truncate_text(child, 80)
                paths.append(entry)
                if depth < depth_limit:
                    walk(child, child_path, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_path = _join_array_path(path, index)
                entry = {"key": child_path, "type": _json_type(child)}
                if isinstance(child, list):
                    entry["length"] = len(child)
                if include_preview and isinstance(child, str):
                    entry["preview"] = truncate_text(child, 80)
                paths.append(entry)
                if depth < depth_limit:
                    walk(child, child_path, depth + 1)

    walk(root, "", 0)
    return {"root_type": _json_type(root), "paths": paths}


def get_summary_key(
    summary_json: str,
    key: str,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Return the addressed summary subtree as text."""
    root = _parse_summary_json(summary_json)
    segments = _parse_summary_key(key)
    effective_max_chars = _require_positive_max_chars(max_chars, error_cls=SummaryContentError)

    node: Any = root
    for kind, value in segments:
        if kind == "field":
            if not isinstance(node, dict) or value not in node:
                raise SummaryContentError(
                    "summary_key_not_found",
                    "Summary key not found",
                    key=key,
                )
            node = node[value]
        else:
            if not isinstance(node, list):
                raise SummaryContentError(
                    "summary_key_not_found",
                    "Summary key not found",
                    key=key,
                )
            index = int(value)
            if index < 0 or index >= len(node):
                raise SummaryContentError(
                    "summary_key_not_found",
                    "Summary key not found",
                    key=key,
                )
            node = node[index]

    content, content_format = _serialize_value(node)
    effective_max_chars = DEFAULT_MAX_CHARS if effective_max_chars is None else effective_max_chars
    truncated = False
    if len(content) > effective_max_chars:
        content = truncate_text(content, effective_max_chars)
        truncated = True

    return {
        "key": key,
        "value_type": _json_type(node),
        "content_format": content_format,
        "content": content,
        "truncated": truncated,
    }


_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})(?:[ \t]+(.*)|$)")


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _slugify_heading(title: str, index: int, used: set[str]) -> str:
    slug_source = title.strip().lower()
    slug_source = re.sub(r"\s+", "-", slug_source)
    filtered: list[str] = []
    for ch in slug_source:
        if "a" <= ch <= "z" or "0" <= ch <= "9" or _is_cjk_char(ch) or ch == "-":
            filtered.append(ch)
    base_slug = re.sub(r"-+", "-", "".join(filtered)).strip("-")
    if not base_slug:
        base_slug = f"section-{index}"
    slug = base_slug
    suffix = 2
    while slug in used:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    used.add(slug)
    return slug


def _parse_fence_marker(line: str) -> tuple[str, int] | None:
    stripped = line.lstrip()
    if not stripped or stripped[0] not in {"`", "~"}:
        return None
    marker = stripped[0]
    length = 0
    while length < len(stripped) and stripped[length] == marker:
        length += 1
    if length < 3:
        return None
    return marker, length


def _scan_markdown_headings(markdown: str) -> tuple[list[str], list[dict[str, Any]]]:
    lines = markdown.splitlines()
    total_lines = len(lines)
    headings: list[dict[str, Any]] = []
    in_fence = False
    fence_char = ""
    fence_length = 0

    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if in_fence:
            marker = _parse_fence_marker(line)
            if marker is not None and marker[0] == fence_char and marker[1] >= fence_length:
                in_fence = False
                fence_char = ""
                fence_length = 0
            continue

        marker = _parse_fence_marker(line)
        if marker is not None:
            in_fence = True
            fence_char, fence_length = marker
            continue

        heading_match = _MARKDOWN_HEADING_RE.match(line)
        if not heading_match:
            continue
        level = len(heading_match.group(1))
        title = (heading_match.group(2) or "").strip()
        headings.append(
            {
                "line_number": line_number,
                "level": level,
                "title": title,
            }
        )

    return lines, headings


def get_markdown_outline(markdown: str) -> dict[str, Any]:
    lines, headings = _scan_markdown_headings(markdown)
    total_lines = len(lines)
    used_ids: set[str] = set()
    sections: list[dict[str, Any]] = []

    for idx, heading in enumerate(headings, start=1):
        next_line = headings[idx]["line_number"] - 1 if idx < len(headings) else total_lines
        sections.append(
            {
                "id": _slugify_heading(heading["title"], idx, used_ids),
                "title": heading["title"],
                "level": heading["level"],
                "start_line": heading["line_number"],
                "end_line": next_line,
            }
        )

    return {"total_lines": total_lines, "sections": sections}


def get_markdown_line_range(markdown: str, start_line: int, end_line: int) -> dict[str, Any]:
    start = int(start_line)
    end = int(end_line)
    if start < 1 or end < 1 or start > end:
        raise MarkdownContentError(
            "invalid_line_range",
            "Line range is invalid",
            start_line=start_line,
            end_line=end_line,
        )

    lines = markdown.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        raise MarkdownContentError(
            "invalid_line_range",
            "Line range is invalid for empty markdown",
            start_line=start_line,
            end_line=end_line,
        )

    actual_start_line = min(start, total_lines)
    actual_end_line = min(end, total_lines)
    content = "\n".join(lines[actual_start_line - 1 : actual_end_line])

    return {
        "start_line": start,
        "end_line": end,
        "actual_start_line": actual_start_line,
        "actual_end_line": actual_end_line,
        "total_lines": total_lines,
        "content": content,
    }
