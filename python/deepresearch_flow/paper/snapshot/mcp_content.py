from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

DEFAULT_MAX_CHARS = 8_000
DEFAULT_SUMMARY_VALUE_MAX_CHARS = 4_000
HARD_SUMMARY_VALUE_MAX_CHARS = 16_000
DEFAULT_SUMMARY_KEY_MAX_PATHS = 200
HARD_SUMMARY_KEY_MAX_PATHS = 500
SUMMARY_KEY_PREVIEW_TOTAL_BUDGET = 4_000
SUMMARY_KEY_PATH_MAX_CHARS = 512
SUMMARY_KEY_PATH_TOTAL_BUDGET = 16_000
DEFAULT_SUMMARY_CHILD_KEYS = 100
HARD_SUMMARY_CHILD_KEYS = 300
SUMMARY_CHILD_KEY_MAX_CHARS = 256
SUMMARY_CHILD_KEY_TOTAL_BUDGET = 8_000
_FORBIDDEN_KEY_CHARS = {".", "[", "]"}
DEFAULT_MAX_WINDOW_LINES_PER_RANGE = 500
DEFAULT_MAX_WINDOW_TOTAL_LINES = 800
DEFAULT_MAX_CHARS_PER_RANGE = 12_000
DEFAULT_MAX_CHARS_TOTAL = 24_000
DEFAULT_MAX_SECTIONS = 200
HARD_MAX_SECTIONS = 500
_VALID_WINDOW_MODES = {"range", "head", "tail", "head_tail", "around"}
_LANG_TAG_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")


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


def _is_actual_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_positive_int(value: Any, *, name: str, code: str = "invalid_line_count") -> int:
    if not _is_actual_int(value) or value <= 0:
        raise MarkdownContentError(code, f"{name} must be a positive integer", **{name: value})
    return int(value)


def _require_non_negative_int(value: Any, *, name: str) -> int:
    if not _is_actual_int(value) or value < 0:
        raise MarkdownContentError(
            "invalid_line_count", f"{name} must be a non-negative integer", **{name: value}
        )
    return int(value)


def _require_optional_positive_line(value: Any, *, name: str) -> int:
    if not _is_actual_int(value) or value <= 0:
        raise MarkdownContentError(
            "invalid_line_range", f"{name} must be a positive integer", **{name: value}
        )
    return int(value)


def _validate_window_budgets(
    *,
    max_window_lines_per_range: Any,
    max_window_total_lines: Any,
    max_chars_per_range: Any,
    max_chars_total: Any,
) -> tuple[int, int, int, int]:
    return (
        _require_positive_int(max_window_lines_per_range, name="max_window_lines_per_range"),
        _require_positive_int(max_window_total_lines, name="max_window_total_lines"),
        _require_positive_int(max_chars_per_range, name="max_chars_per_range"),
        _require_positive_int(max_chars_total, name="max_chars_total"),
    )


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
            raise SummaryContentError(
                "invalid_summary_key", "Summary key has an empty field name", key=key
            )
        if ch == "[":
            end = text.find("]", i + 1)
            if end == -1:
                raise SummaryContentError(
                    "invalid_summary_key", "Summary key has an unterminated array index", key=key
                )
            index_text = text[i + 1 : end]
            if not index_text.isdigit():
                raise SummaryContentError(
                    "invalid_summary_key",
                    "Summary key array indexes must be non-negative integers",
                    key=key,
                )
            segments.append(("index", int(index_text)))
            i = end + 1
            expecting_segment = False
            if i < length:
                if text[i] == ".":
                    i += 1
                    if i >= length:
                        raise SummaryContentError(
                            "invalid_summary_key", "Summary key has an empty field name", key=key
                        )
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
            raise SummaryContentError(
                "invalid_summary_key", "Summary key has an empty field name", key=key
            )
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
                    raise SummaryContentError(
                        "invalid_summary_key", "Summary key has an empty field name", key=key
                    )
                expecting_segment = True
            elif text[i] == "[":
                continue
            else:
                raise SummaryContentError(
                    "invalid_summary_key", "Summary key contains invalid syntax", key=key
                )

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


def _require_summary_int(
    value: Any,
    *,
    name: str,
    code: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not _is_actual_int(value):
        raise SummaryContentError(code, f"{name} must be an integer", **{name: value})
    result = int(value)
    if minimum is not None and result < minimum:
        raise SummaryContentError(
            code, f"{name} is below the minimum", **{name: value}, minimum=minimum
        )
    if maximum is not None and result > maximum:
        raise SummaryContentError(
            code, f"{name} exceeds the maximum", **{name: value}, maximum=maximum
        )
    return result


def _require_summary_bool(value: Any, *, name: str, code: str) -> bool:
    if not isinstance(value, bool):
        raise SummaryContentError(code, f"{name} must be a boolean", **{name: value})
    return value


def _resolve_summary_node(root: Any, key: str) -> Any:
    if key == "$":
        raise SummaryContentError(
            "invalid_summary_key", "Root summary selector is not supported", key=key
        )
    segments = _parse_summary_key(key)
    node: Any = root
    for kind, value in segments:
        if kind == "field":
            if not isinstance(node, dict) or value not in node:
                raise SummaryContentError("summary_key_not_found", "Summary key not found", key=key)
            node = node[value]
        else:
            if not isinstance(node, list):
                raise SummaryContentError("summary_key_not_found", "Summary key not found", key=key)
            index = int(value)
            if index < 0 or index >= len(node):
                raise SummaryContentError("summary_key_not_found", "Summary key not found", key=key)
            node = node[index]
    return node


def _bounded_key_text(text: str, *, per_key_budget: int, remaining_total: int) -> tuple[str, bool]:
    budget = min(per_key_budget, remaining_total)
    if budget <= 0:
        return "", True
    truncated = len(text) > budget
    return text[:budget], truncated


def get_summary_keys(
    summary_json: str,
    max_depth: int = 2,
    include_preview: bool = False,
    max_paths: int = DEFAULT_SUMMARY_KEY_MAX_PATHS,
) -> dict[str, Any]:
    """Return bounded summary key paths in source order."""
    root = _parse_summary_json(summary_json)
    depth_limit = _require_summary_int(
        max_depth, name="max_depth", code="invalid_max_depth", minimum=0, maximum=4
    )
    include_preview = _require_summary_bool(
        include_preview, name="include_preview", code="invalid_include_preview"
    )
    path_limit = _require_summary_int(
        max_paths,
        name="max_paths",
        code="invalid_max_paths",
        minimum=1,
        maximum=HARD_SUMMARY_KEY_MAX_PATHS,
    )

    paths: list[dict[str, Any]] = []
    total_paths = 0
    path_chars_used = 0
    preview_chars_used = 0
    paths_truncated = False
    previews_truncated = False

    def add_entry(child_path: str, child: Any) -> None:
        nonlocal \
            total_paths, \
            path_chars_used, \
            preview_chars_used, \
            paths_truncated, \
            previews_truncated
        total_paths += 1

        remaining_path_chars = SUMMARY_KEY_PATH_TOTAL_BUDGET - path_chars_used
        if len(paths) >= path_limit or remaining_path_chars <= 0:
            paths_truncated = True
            return

        bounded_path, path_was_truncated = _bounded_key_text(
            child_path,
            per_key_budget=SUMMARY_KEY_PATH_MAX_CHARS,
            remaining_total=remaining_path_chars,
        )
        paths_truncated = paths_truncated or path_was_truncated
        if not bounded_path:
            paths_truncated = True
            return

        entry: dict[str, Any] = {"key": bounded_path, "type": _json_type(child)}
        path_chars_used += len(bounded_path)
        if isinstance(child, list):
            entry["length"] = len(child)
        if include_preview and isinstance(child, str):
            remaining_preview_chars = SUMMARY_KEY_PREVIEW_TOTAL_BUDGET - preview_chars_used
            if remaining_preview_chars > 0:
                preview_budget = min(80, remaining_preview_chars)
                preview = truncate_text(child, preview_budget)
                entry["preview"] = preview
                preview_chars_used += len(preview)
                if len(child) > preview_budget:
                    previews_truncated = True
            else:
                previews_truncated = True
        paths.append(entry)

    def walk(value: Any, path: str, depth: int) -> None:
        if isinstance(value, dict):
            for field_name, child in value.items():
                field_name = str(field_name)
                child_path = _join_object_path(path, field_name)
                _validate_field_name(field_name, path=child_path)
                add_entry(child_path, child)
                if depth < depth_limit:
                    walk(child, child_path, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_path = _join_array_path(path, index)
                add_entry(child_path, child)
                if depth < depth_limit:
                    walk(child, child_path, depth + 1)

    walk(root, "", 0)
    returned_paths = len(paths)
    paths_truncated = paths_truncated or returned_paths < total_paths
    truncated = paths_truncated or previews_truncated
    return {
        "root_type": _json_type(root),
        "paths": paths,
        "total_paths": total_paths,
        "returned_paths": returned_paths,
        "truncated": truncated,
        "paths_truncated": paths_truncated,
        "previews_truncated": previews_truncated,
        "max_depth": depth_limit,
        "max_paths": path_limit,
        "path_char_budget": SUMMARY_KEY_PATH_TOTAL_BUDGET,
        "preview_char_budget": SUMMARY_KEY_PREVIEW_TOTAL_BUDGET if include_preview else 0,
    }


def _child_key_candidates(node: Any) -> list[str]:
    if isinstance(node, dict):
        result: list[str] = []
        for field_name in node:
            field_name = str(field_name)
            _validate_field_name(field_name, path=field_name)
            result.append(field_name)
        return result
    if isinstance(node, list):
        return [f"[{index}]" for index in range(len(node))]
    return []


def _bounded_child_keys(node: Any, max_child_keys: int) -> dict[str, Any]:
    candidates = _child_key_candidates(node)
    child_keys: list[str] = []
    total_chars = 0
    children_truncated = False
    for child_key in candidates:
        if len(child_keys) >= max_child_keys:
            children_truncated = True
            break
        remaining = SUMMARY_CHILD_KEY_TOTAL_BUDGET - total_chars
        bounded, truncated = _bounded_key_text(
            child_key,
            per_key_budget=SUMMARY_CHILD_KEY_MAX_CHARS,
            remaining_total=remaining,
        )
        if not bounded:
            children_truncated = True
            break
        child_keys.append(bounded)
        total_chars += len(bounded)
        children_truncated = children_truncated or truncated
    if len(child_keys) < len(candidates):
        children_truncated = True
    return {
        "child_keys": child_keys,
        "child_count": len(candidates),
        "returned_child_keys": len(child_keys),
        "children_truncated": children_truncated,
    }


def get_summary_value(
    summary_json: str,
    key: str,
    max_chars: int | None = DEFAULT_SUMMARY_VALUE_MAX_CHARS,
    include_subtree: bool = False,
    max_child_keys: int = DEFAULT_SUMMARY_CHILD_KEYS,
) -> dict[str, Any]:
    """Return a bounded selected summary value or child-key metadata."""
    root = _parse_summary_json(summary_json)
    node = _resolve_summary_node(root, key)
    effective_max_chars = (
        DEFAULT_SUMMARY_VALUE_MAX_CHARS
        if max_chars is None
        else _require_summary_int(
            max_chars,
            name="max_chars",
            code="invalid_max_chars",
            minimum=1,
            maximum=HARD_SUMMARY_VALUE_MAX_CHARS,
        )
    )
    include_subtree = _require_summary_bool(
        include_subtree, name="include_subtree", code="invalid_include_subtree"
    )
    effective_max_child_keys = _require_summary_int(
        max_child_keys,
        name="max_child_keys",
        code="invalid_max_child_keys",
        minimum=1,
        maximum=HARD_SUMMARY_CHILD_KEYS,
    )

    value_type = _json_type(node)
    if isinstance(node, (dict, list)) and not include_subtree:
        return {
            "key": key,
            "value_type": value_type,
            "content_format": None,
            "content": None,
            **_bounded_child_keys(node, effective_max_child_keys),
            "truncated": False,
        }

    content, content_format = _serialize_value(node)
    content_is_valid_json = content_format == "application/json"
    truncated = False
    if len(content) > effective_max_chars:
        content = truncate_text(content, effective_max_chars)
        truncated = True
        if isinstance(node, (dict, list)):
            content_format = "text/plain"
            content_is_valid_json = False

    result: dict[str, Any] = {
        "key": key,
        "value_type": value_type,
        "content_format": content_format,
        "content": content,
        "truncated": truncated,
    }
    if isinstance(node, (dict, list)):
        result["content_is_valid_json"] = content_is_valid_json
    return result


def get_summary_key(
    summary_json: str,
    key: str,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Return the addressed summary subtree as text (legacy helper)."""
    max_chars = DEFAULT_MAX_CHARS if max_chars is None else max_chars
    return get_summary_value(summary_json, key, max_chars=max_chars, include_subtree=True)


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


def _validate_max_sections(max_sections: Any) -> int:
    if not _is_actual_int(max_sections) or max_sections <= 0 or max_sections > HARD_MAX_SECTIONS:
        raise MarkdownContentError(
            "invalid_section_count",
            f"max_sections must be a positive integer no greater than {HARD_MAX_SECTIONS}",
            max_sections=max_sections,
        )
    return int(max_sections)


def get_markdown_outline(markdown: str, max_sections: int = DEFAULT_MAX_SECTIONS) -> dict[str, Any]:
    lines, headings = _scan_markdown_headings(markdown)
    total_lines = len(lines)
    section_limit = _validate_max_sections(max_sections)
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

    total_sections = len(sections)
    returned_sections = min(total_sections, section_limit)
    return {
        "total_lines": total_lines,
        "sections": sections[:section_limit],
        "total_sections": total_sections,
        "returned_sections": returned_sections,
        "truncated": returned_sections < total_sections,
    }


def _reject_irrelevant_window_params(mode: str, params: dict[str, Any]) -> None:
    defaults = {
        "start_line": None,
        "end_line": None,
        "line_count": 80,
        "head_lines": 40,
        "tail_lines": 40,
        "center_line": None,
        "before_lines": 40,
        "after_lines": 40,
    }
    relevant = {
        "range": {"start_line", "end_line"},
        "head": {"line_count"},
        "tail": {"line_count"},
        "head_tail": {"head_lines", "tail_lines"},
        "around": {"center_line", "before_lines", "after_lines"},
    }[mode]
    for name, value in params.items():
        if name not in relevant and value != defaults[name]:
            raise MarkdownContentError(
                "invalid_line_range",
                f"{name} is not valid for {mode} windows",
                mode=mode,
                parameter=name,
            )


def _merge_line_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + 1:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _ensure_line_budget(
    ranges: list[tuple[int, int]],
    *,
    max_window_lines_per_range: int,
    max_window_total_lines: int,
) -> None:
    total = 0
    for start, end in ranges:
        count = end - start + 1
        if count > max_window_lines_per_range:
            raise MarkdownContentError(
                "window_too_large",
                "Window range exceeds the per-range line budget",
                start_line=start,
                end_line=end,
                line_count=count,
                max_window_lines_per_range=max_window_lines_per_range,
            )
        total += count
    if total > max_window_total_lines:
        raise MarkdownContentError(
            "window_too_large",
            "Window ranges exceed the total line budget",
            line_count=total,
            max_window_total_lines=max_window_total_lines,
        )


def _materialize_ranges(
    lines: list[str],
    ranges: list[tuple[int, int]],
    *,
    max_chars_per_range: int,
    max_chars_total: int,
) -> tuple[list[dict[str, Any]], bool]:
    remaining_total_chars = max_chars_total
    output: list[dict[str, Any]] = []
    any_truncated = False
    for start, end in ranges:
        content = "\n".join(lines[start - 1 : end])
        char_limit = min(max_chars_per_range, max(remaining_total_chars, 0))
        truncated_by_chars = len(content) > char_limit
        if truncated_by_chars:
            content = content[:char_limit]
            any_truncated = True
        remaining_total_chars -= len(content)
        output.append(
            {
                "start_line": start,
                "end_line": end,
                "content": content,
                "truncated_by_chars": truncated_by_chars,
            }
        )
    return output, any_truncated


def compute_content_window(
    text: str,
    *,
    mode: str = "head",
    start_line: int | None = None,
    end_line: int | None = None,
    line_count: int = 80,
    head_lines: int = 40,
    tail_lines: int = 40,
    center_line: int | None = None,
    before_lines: int = 40,
    after_lines: int = 40,
    max_window_lines_per_range: int = DEFAULT_MAX_WINDOW_LINES_PER_RANGE,
    max_window_total_lines: int = DEFAULT_MAX_WINDOW_TOTAL_LINES,
    max_chars_per_range: int = DEFAULT_MAX_CHARS_PER_RANGE,
    max_chars_total: int = DEFAULT_MAX_CHARS_TOTAL,
) -> dict[str, Any]:
    """Compute bounded 1-based line windows from decoded markdown/text."""
    if mode not in _VALID_WINDOW_MODES:
        raise MarkdownContentError("invalid_window_mode", "Window mode is invalid", mode=mode)

    (
        effective_max_lines_per_range,
        effective_max_total_lines,
        effective_max_chars_per_range,
        effective_max_chars_total,
    ) = _validate_window_budgets(
        max_window_lines_per_range=max_window_lines_per_range,
        max_window_total_lines=max_window_total_lines,
        max_chars_per_range=max_chars_per_range,
        max_chars_total=max_chars_total,
    )
    _reject_irrelevant_window_params(
        mode,
        {
            "start_line": start_line,
            "end_line": end_line,
            "line_count": line_count,
            "head_lines": head_lines,
            "tail_lines": tail_lines,
            "center_line": center_line,
            "before_lines": before_lines,
            "after_lines": after_lines,
        },
    )

    lines = text.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        raise MarkdownContentError("invalid_line_range", "Line windows are invalid for empty text")

    selected: list[tuple[int, int]]
    if mode == "range":
        start = _require_optional_positive_line(start_line, name="start_line")
        end = _require_optional_positive_line(end_line, name="end_line")
        if start > end or end > total_lines:
            raise MarkdownContentError(
                "invalid_line_range",
                "Line range must be inside the document",
                start_line=start_line,
                end_line=end_line,
                total_lines=total_lines,
            )
        selected = [(start, end)]
    elif mode == "head":
        count = _require_positive_int(line_count, name="line_count")
        selected = [(1, min(count, total_lines))]
    elif mode == "tail":
        count = _require_positive_int(line_count, name="line_count")
        selected = [(max(total_lines - count + 1, 1), total_lines)]
    elif mode == "head_tail":
        head_count = _require_positive_int(head_lines, name="head_lines")
        tail_count = _require_positive_int(tail_lines, name="tail_lines")
        selected = _merge_line_ranges(
            [
                (1, min(head_count, total_lines)),
                (max(total_lines - tail_count + 1, 1), total_lines),
            ]
        )
    else:
        center = _require_optional_positive_line(center_line, name="center_line")
        before = _require_non_negative_int(before_lines, name="before_lines")
        after = _require_non_negative_int(after_lines, name="after_lines")
        if center > total_lines:
            raise MarkdownContentError(
                "invalid_line_range",
                "center_line must be inside the document",
                center_line=center_line,
                total_lines=total_lines,
            )
        selected = [(max(center - before, 1), min(center + after, total_lines))]

    _ensure_line_budget(
        selected,
        max_window_lines_per_range=effective_max_lines_per_range,
        max_window_total_lines=effective_max_total_lines,
    )
    ranges, truncated_by_chars = _materialize_ranges(
        lines,
        selected,
        max_chars_per_range=effective_max_chars_per_range,
        max_chars_total=effective_max_chars_total,
    )
    covers_document = len(selected) == 1 and selected[0] == (1, total_lines)
    return {
        "mode": mode,
        "total_lines": total_lines,
        "ranges": ranges,
        "truncated": (not covers_document) or truncated_by_chars,
        "truncated_by_chars": truncated_by_chars,
    }


def resolve_content_language(
    content_type: str,
    lang: str | None,
    available_translation_langs: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate and normalize content type/language selection for content helpers."""
    if content_type not in {"source", "translation"}:
        raise MarkdownContentError(
            "invalid_content_type", "Content type is invalid", content_type=content_type
        )
    if content_type == "source":
        if lang is not None:
            raise MarkdownContentError(
                "invalid_lang_for_source", "Source content does not accept lang", lang=lang
            )
        return {"content_type": "source", "lang": None}

    if lang is None or not isinstance(lang, str) or not lang.strip():
        raise MarkdownContentError("missing_lang", "Translation content requires lang", lang=lang)
    normalized_lang = lang.strip().lower()
    if not _LANG_TAG_RE.fullmatch(normalized_lang):
        raise MarkdownContentError("invalid_lang", "Language tag is invalid", lang=lang)

    normalized_available: dict[str, str] = {}
    duplicate_tags: set[str] = set()
    for available in available_translation_langs:
        normalized = str(available).strip().lower()
        if normalized in normalized_available:
            duplicate_tags.add(normalized)
        else:
            normalized_available[normalized] = str(available)
    if normalized_lang in duplicate_tags:
        raise MarkdownContentError(
            "translation_not_available",
            "Translation language tags are ambiguous after normalization",
            lang=normalized_lang,
            diagnostic=f"duplicate_normalized_lang:{normalized_lang}",
        )
    if normalized_lang not in normalized_available:
        raise MarkdownContentError(
            "translation_not_available", "Translation is not available", lang=normalized_lang
        )
    return {"content_type": "translation", "lang": normalized_lang}


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
