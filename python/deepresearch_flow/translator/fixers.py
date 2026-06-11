"""OCR markdown repair utilities."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


class ReferenceProcessor:
    def __init__(self) -> None:
        self._patterns = {
            "reference_def": re.compile(
                r"^\[(\d+)\]((?:(?!\[\d+\])[^\n])*)\n(?=^\[\d+\]|$)",
                re.MULTILINE,
            ),
            "reference_range": re.compile(r"\[(\d+)\-(\d+)\]"),
            "reference_split_range": re.compile(r"\[(\d+)\]\s*[-–—]\s*\[(\d+)\]"),
            "reference_multi": re.compile(r"\[(\d+(?:,\s*\d+)*)\]"),
            "reference_single": re.compile(r"\[(\d+)\]"),
        }

    def fix_references(self, text: str) -> str:
        def _sub(pattern: re.Pattern, repl) -> str:
            protected = _build_protected_ranges(text, include_inline_math=True)

            def _replace(match: re.Match) -> str:
                if _in_protected(match.start(), protected):
                    return match.group(0)
                return repl(match)

            return pattern.sub(_replace, text)

        text = _sub(
            self._patterns["reference_def"],
            lambda match: f"[^{match.group(1)}]: {match.group(2).strip()}\n",
        )
        text = _sub(
            self._patterns["reference_range"],
            lambda match: " ".join(
                f"[^{i}]" for i in range(int(match.group(1)), int(match.group(2)) + 1)
            ),
        )
        text = _sub(
            self._patterns["reference_split_range"],
            lambda match: " ".join(
                f"[^{i}]" for i in range(int(match.group(1)), int(match.group(2)) + 1)
            ),
        )
        text = _sub(
            self._patterns["reference_multi"],
            lambda match: " ".join(f"[^{n.strip()}]" for n in match.group(1).split(",")),
        )
        text = _sub(
            self._patterns["reference_single"],
            lambda match: f"[^{match.group(1)}]",
        )
        return text


class LinkProcessor:
    def __init__(self) -> None:
        self._patterns = {
            "url": re.compile(
                r"(?<!<)(?<!]\()(?:(?<=^)|(?<=\s)|(?<=[\(\[{\"“]))"
                r"(https?://\S+)"
            ),
            "email": re.compile(
                r"(?<!<)(?<!]\()(?<![\w.%+-])"
                r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
                r"(?=[\s\)\]\}>.,!?;:，。！？；：]|$)"
            ),
            "phone": re.compile(
                r"(?<!<)(?<!]\()(?:(?<=^)|(?<=\s)|(?<=[\(\[{\"“]))"
                r"(\+?\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})"
                r"(?=[\s\)\]\}>.,!?;:，。！？；：]|$)"
            ),
        }

    def fix_links(self, text: str) -> str:
        def _sub(pattern: re.Pattern, repl) -> str:
            protected = _build_protected_ranges(text, include_inline_math=True)

            def _replace(match: re.Match) -> str:
                if _in_protected(match.start(), protected):
                    return match.group(0)
                return repl(match)

            return pattern.sub(_replace, text)

        def bracket_urls(value: str) -> str:
            def repl(match: re.Match) -> str:
                url = match.group(1)
                suffix = ""
                while url:
                    if url[-1] in ".,!?;:，。！？；：":
                        suffix = url[-1] + suffix
                        url = url[:-1]
                        continue
                    if url.endswith(")") and url.count("(") < url.count(")"):
                        suffix = ")" + suffix
                        url = url[:-1]
                        continue
                    if url.endswith("]") and url.count("[") < url.count("]"):
                        suffix = "]" + suffix
                        url = url[:-1]
                        continue
                    if url.endswith("}") and url.count("{") < url.count("}"):
                        suffix = "}" + suffix
                        url = url[:-1]
                        continue
                    break
                return f"<{url}>{suffix}"

            return _sub(self._patterns["url"], repl)

        def bracket_emails(value: str) -> str:
            return _sub(self._patterns["email"], lambda match: f"<mailto:{match.group(1)}>")

        def bracket_phones(value: str) -> str:
            return _sub(self._patterns["phone"], lambda match: f"<tel:{match.group(1)}>")

        text = bracket_urls(text)
        text = bracket_emails(text)
        text = bracket_phones(text)
        return text


class PseudocodeProcessor:
    def __init__(self) -> None:
        self._header_pattern = re.compile(
            r"^\s*\*?\*?\s*(Algorithm|算法)"
            r"(?:\s+(\d+(?:\.\d+)*|[IVX]+|[A-Z]))"
            r"\*?\*?(?:\s*[:.)-]\s*|\s+)(.*)$",
            re.IGNORECASE,
        )

    def wrap_pseudocode_blocks(self, text: str, lang: str = "pseudo") -> str:
        lines = text.splitlines()
        out: list[str] = []
        i = 0
        in_fence = False

        while i < len(lines):
            line = lines[i]
            if line.strip().startswith("```"):
                in_fence = not in_fence
                out.append(line)
                i += 1
                continue

            if not in_fence and self._header_pattern.match(line):
                header_line = line
                block = [header_line]
                i += 1
                while i < len(lines):
                    peek = lines[i]
                    if peek.strip().startswith("```") or re.match(r"^\s*#{1,6}\s", peek):
                        break
                    if not self._is_algo_continuation(peek):
                        break
                    block.append(peek)
                    i += 1

                out.append(f"```{lang}")
                title = self._format_title(header_line)
                if title:
                    out.append(f"// {title}")
                for raw in block[1:]:
                    s = raw.strip()
                    if s == "***":
                        out.append("// " + "-" * 40)
                        continue
                    out.append(self._clean_inline(raw))
                out.append("```")
                continue

            out.append(line)
            i += 1

        return "\n".join(out)

    def _format_title(self, header_line: str) -> str | None:
        match = self._header_pattern.match(header_line)
        if not match:
            return None
        alg_no = (match.group(2) or "").strip()
        rest = (match.group(3) or "").strip()
        rest = self._clean_inline(rest)
        if alg_no and rest:
            return f"Algorithm {alg_no}: {rest}"
        if alg_no:
            return f"Algorithm {alg_no}"
        if rest:
            return f"Algorithm: {rest}"
        return "Algorithm"

    def _is_algo_continuation(self, line: str) -> bool:
        s = line.strip()
        if s == "" or s == "***":
            return True
        if re.match(r"^\s*\d+\s*[:.)]\ ", s):
            return True
        if re.match(r"^\s*(Input|Output|Require|Ensure):\s*", s, re.I):
            return True
        if re.match(
            r"^\s*(function|procedure|for|while|if|else|repeat|return|end)\b",
            s,
            re.I,
        ):
            return True
        return False

    def _clean_inline(self, text: str) -> str:
        text = re.sub(
            r"<\s*sub\s*>\s*(.*?)\s*<\s*/\s*sub\s*>",
            lambda m: "_" + re.sub(r"\*", "", m.group(1)),
            text,
            flags=re.I,
        )
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^\*]+)\*", r"\1", text)
        text = re.sub(r"\*+$", "", text)
        text = re.sub(r"^\*+", "", text)
        return text.strip()


class TitleProcessor:
    def __init__(self) -> None:
        self._patterns = {
            "roman_with_sec": re.compile(
                r"^(#{1,6})?\s*(Sec(?:tion)?\.\s*)?([IVX]+(?:\.[IVX]+)*)(\.?)\s+(.+)$"
            ),
            "number": re.compile(r"^\s*(#{1,6})?\s*(\d+(?:\.\d+)*)(\.?)\s+(.+)$"),
            "letter_upper": re.compile(r"^(#{1,6})?\s*([A-Z])\.\s+(.+)$"),
            "letter_lower": re.compile(r"^(#{1,6})?\s*([a-z])\.\s+(.+)$"),
        }

    def fix_titles(self, text: str) -> str:
        lines = text.split("\n")
        new_lines: list[str] = []
        in_fence = False

        def is_title(line: str) -> bool:
            return re.match(r"^#{1,6}\s+", line) is not None

        has_roman = bool(
            re.search(
                r"^#{1,6}?\s*(?:Sec(?:tion)?\.\s*)?[IVX]+(?:\.[IVX]+)*\.?\s+",
                text,
                re.MULTILINE,
            )
        )

        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                new_lines.append(line)
                continue
            if in_fence:
                new_lines.append(line)
                continue
            if not is_title(line):
                new_lines.append(line)
                continue
            modified = False

            match = self._patterns["roman_with_sec"].match(line)
            if match:
                section_prefix = match.group(2) or ""
                roman_num = match.group(3)
                dot = match.group(4)
                title = match.group(5)
                level = len(roman_num.split(".")) + 1
                new_hashes = "#" * level
                new_line = f"{new_hashes} {section_prefix}{roman_num}{dot or '.'} {title}"
                new_lines.append(new_line)
                modified = True

            if not modified:
                match = self._patterns["number"].match(line)
                if match:
                    number = match.group(2)
                    dot = match.group(3)
                    title = match.group(4)
                    level = len(number.split(".")) + 1
                    if has_roman:
                        level += 1
                    new_hashes = "#" * min(level, 6)
                    trail_dot = dot if has_roman else (dot or ".")
                    new_line = f"{new_hashes} {number}{trail_dot} {title}"
                    new_lines.append(new_line)
                    modified = True

            if not modified:
                for pattern_name in ["letter_upper", "letter_lower"]:
                    match = self._patterns[pattern_name].match(line)
                    if match and not re.match(r"^[A-Z][a-z]", match.group(3)):
                        letter = match.group(2)
                        title = match.group(3)
                        level = 4 if pattern_name == "letter_upper" else 5
                        new_hashes = "#" * level
                        new_line = f"{new_hashes} {letter}. {title}"
                        new_lines.append(new_line)
                        modified = True
                        break

            if not modified:
                new_lines.append(line)

        return "\n".join(new_lines)


_HEADING_RE = re.compile(r"^(\s{0,3})(#{1,6})(\s+)(.*)$")


def preserve_heading_levels(original: str, formatted: str) -> str:
    original_lines = original.split("\n")
    formatted_lines = formatted.split("\n")

    def collect_hashes(lines: list[str]) -> list[str]:
        hashes: list[str] = []
        in_fence = False
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = _HEADING_RE.match(line)
            if match:
                hashes.append(match.group(2))
        return hashes

    original_hashes = collect_hashes(original_lines)
    if not original_hashes:
        return formatted

    out: list[str] = []
    in_fence = False
    heading_idx = 0
    for line in formatted_lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        match = _HEADING_RE.match(line)
        if match and heading_idx < len(original_hashes):
            out.append(
                f"{match.group(1)}{original_hashes[heading_idx]}{match.group(3)}{match.group(4)}"
            )
            heading_idx += 1
            continue
        if match:
            heading_idx += 1
        out.append(line)

    return "\n".join(out)


@dataclass
class Block:
    kind: str
    content: str


def _is_blank(line: str) -> bool:
    return len(line.strip()) == 0


def _line_starts_with_fence(line: str) -> Optional[str]:
    match = re.match(r"^\s*(`{3,}|~{3,})", line)
    return match.group(1) if match else None


def _looks_like_table_header(line: str) -> bool:
    return "|" in line


def _looks_like_table_delim(line: str) -> bool:
    return re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line) is not None


def _is_image_line(line: str) -> bool:
    return re.match(r"^\s*!\[.*?\]\(.*?\)\s*$", line) is not None


def _parse_blocks(text: str) -> list[Block]:
    lines = text.splitlines(keepends=True)
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if _is_blank(line):
            blocks.append(Block(kind="sep", content=line))
            i += 1
            continue

        if line.strip() == "---":
            blocks.append(Block(kind="page", content=line))
            i += 1
            continue

        fence = _line_starts_with_fence(line)
        if fence:
            j = i + 1
            while j < n and not re.match(rf"^\s*{re.escape(fence)}", lines[j]):
                j += 1
            if j < n:
                block = "".join(lines[i : j + 1])
                blocks.append(Block(kind="code", content=block))
                i = j + 1
                continue
            block = "".join(lines[i:])
            blocks.append(Block(kind="code", content=block))
            break

        if _is_image_line(line):
            blocks.append(Block(kind="image", content=line))
            i += 1
            continue

        if i + 1 < n and _looks_like_table_header(line) and _looks_like_table_delim(lines[i + 1]):
            j = i + 2
            while j < n and ("|" in lines[j]) and not _is_blank(lines[j]):
                j += 1
            block = "".join(lines[i:j])
            blocks.append(Block(kind="table", content=block))
            i = j
            continue

        if line.strip() == "$$":
            j = i + 1
            while j < n and lines[j].strip() != "$$":
                j += 1
            if j < n:
                block = "".join(lines[i : j + 1])
                blocks.append(Block(kind="math", content=block))
                i = j + 1
                continue
            block = "".join(lines[i:])
            blocks.append(Block(kind="math", content=block))
            break

        text_lines = [line]
        j = i + 1
        while j < n:
            peek = lines[j]
            if _is_blank(peek) or _is_image_line(peek) or peek.strip() == "---":
                break
            if _line_starts_with_fence(peek):
                break
            if (
                j + 1 < n
                and _looks_like_table_header(peek)
                and _looks_like_table_delim(lines[j + 1])
            ):
                break
            if peek.strip() == "$$":
                break
            text_lines.append(peek)
            j += 1
        blocks.append(Block(kind="text", content="".join(text_lines)))
        i = j

    return blocks


def _word_set(text: str) -> set[str]:
    return {w for w in re.split(r"\W+", text.lower()) if w}


def _split_confidence(before_text: str, after_text: str) -> float:
    confidence = 0.0
    if not re.search(r"[.!?。！？]\s*$", before_text):
        confidence += 0.3
    if after_text and after_text[0].islower():
        confidence += 0.4
    common_words = len(_word_set(before_text) & _word_set(after_text))
    if common_words > 1:
        confidence += min(0.3, common_words * 0.1)
    return min(confidence, 1.0)


def _merge_blocks(blocks: list[Block]) -> list[Block]:
    idx = 0
    while idx + 2 < len(blocks):
        before = blocks[idx]
        middle = blocks[idx + 1]
        after = blocks[idx + 2]
        if before.kind != "text" or after.kind != "text":
            idx += 1
            continue
        if middle.kind not in {"page", "image", "table", "code"}:
            idx += 1
            continue

        before_text = before.content.strip()
        after_text = after.content.strip()
        if before_text == "" or after_text == "":
            idx += 1
            continue

        if middle.kind == "page" and before_text.endswith("-") and after_text[0].islower():
            merged_text = before.content.rstrip("-") + after.content.lstrip()
            blocks = blocks[:idx] + [Block(kind="text", content=merged_text)] + blocks[idx + 3 :]
            continue

        confidence = _split_confidence(before_text, after_text)
        if confidence < 0.7:
            idx += 1
            continue

        merged_text = before.content.rstrip() + " " + after.content.lstrip()
        if middle.kind == "page":
            blocks = blocks[:idx] + [Block(kind="text", content=merged_text)] + blocks[idx + 3 :]
            continue

        blocks = (
            blocks[:idx] + [Block(kind="text", content=merged_text), middle] + blocks[idx + 3 :]
        )
        idx += 1

    return blocks


def merge_paragraphs(text: str) -> str:
    blocks = _parse_blocks(text)
    merged = _merge_blocks(blocks)
    return "".join(block.content for block in merged)


# ---------------------------------------------------------------------------
# PaddleOCR-compatible cleanup rules (idempotent, safe for all OCR backends)
# ---------------------------------------------------------------------------

_RE_NESTED_MAILTO = re.compile(r"<mailto:(?:<mailto:)+([^<>]+?)>+")

_RE_FENCED_BLOCK = re.compile(r"^(`{3,}|~{3,}).*?^\1\s*$", re.MULTILINE | re.DOTALL)
_RE_DISPLAY_MATH = re.compile(r"\$\$[\s\S]+?\$\$")
_RE_HTML_CODE = re.compile(r"<code>.*?</code>", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"(`+)(.*?)(\1)", re.DOTALL)
_RE_AUTOLINK = re.compile(r"<https?://[^>]+>|<mailto:[^<>]+>|<tel:[^>]+>")


def _scan_latex_delimited_ranges(
    text: str, open_delim: str, close_delim: str
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = text.find(open_delim, start)
        if idx < 0:
            break
        end = text.find(close_delim, idx + len(open_delim))
        if end < 0:
            start = idx + len(open_delim)
            continue
        ranges.append((idx, end + len(close_delim)))
        start = end + len(close_delim)
    return ranges


def _build_protected_ranges(text: str, *, include_inline_math: bool) -> list[tuple[int, int]]:
    """Return sorted list of (start, end) ranges that must not be modified."""
    ranges: list[tuple[int, int]] = []
    patterns = [
        _RE_FENCED_BLOCK,
        _RE_DISPLAY_MATH,
        _RE_HTML_CODE,
        _RE_INLINE_CODE,
        _RE_AUTOLINK,
    ]
    if include_inline_math:
        patterns.append(_RE_INLINE_MATH)
    for pattern in patterns:
        for m in pattern.finditer(text):
            ranges.append((m.start(), m.end()))
    ranges.extend(_scan_latex_delimited_ranges(text, r"\(", r"\)"))
    ranges.extend(_scan_latex_delimited_ranges(text, r"\[", r"\]"))
    ranges.sort()
    return ranges


def _in_protected(pos: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start > pos:
            return False
        if start <= pos < end:
            return True
    return False


def fix_nested_mailto(text: str) -> str:
    """Collapse nested ``<mailto:<mailto:...>>`` to a single ``<mailto:addr>``."""
    protected = _build_protected_ranges(text, include_inline_math=True)

    def _replace(match: re.Match) -> str:
        if _in_protected(match.start(), protected):
            return match.group(0)
        return f"<mailto:{match.group(1)}>"

    return _RE_NESTED_MAILTO.sub(_replace, text)


_RE_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)(.*?)\$(?!\$)")

# Content that has NO LaTeX syntax at all (no \, ^, _, {, })
_RE_NO_LATEX = re.compile(r"^[^\\^_{}]*$")
# Content that is only footnote references, commas, spaces
_RE_ONLY_REFS = re.compile(r"^[\s,]*(\[\^?\d+\][\s,]*)+$")
# Content that is only punctuation and/or plain words
_RE_ONLY_PUNCT_WORDS = re.compile(r"^[\s.,;:!?a-zA-Z]*$")


def fix_non_math_in_delimiters(text: str) -> str:
    """Strip ``$ ... $`` around content that is not actually math."""
    protected = _build_protected_ranges(text, include_inline_math=False)

    def _replace(m: re.Match) -> str:
        if _in_protected(m.start(), protected):
            return m.group(0)
        inner = m.group(1).strip()
        if not inner:
            return ""
        if _RE_ONLY_REFS.match(inner):
            return inner
        if _RE_NO_LATEX.match(inner) and _RE_ONLY_PUNCT_WORDS.match(inner):
            return inner
        return m.group(0)

    return _RE_INLINE_MATH.sub(_replace, text)


def fix_math_delimiter_spaces(text: str) -> str:
    """Trim extra spaces inside inline ``$ ... $`` delimiters."""
    protected = _build_protected_ranges(text, include_inline_math=False)

    def _replace(m: re.Match) -> str:
        if _in_protected(m.start(), protected):
            return m.group(0)
        inner = m.group(1)
        stripped = inner.strip()
        if not stripped:
            return m.group(0)
        if stripped == inner:
            return m.group(0)
        return f"${stripped}$"

    return _RE_INLINE_MATH.sub(_replace, text)


_RE_TD_INLINE_MATH = re.compile(r"(<td[^>]*>)(.*?)(</td>)", re.DOTALL)


def _apply_transform_outside_protected_ranges(
    text: str,
    protected: list[tuple[int, int]],
    transform,
    *,
    start_offset: int = 0,
) -> str:
    if not protected:
        return transform(text)

    text_start = start_offset
    text_end = start_offset + len(text)
    out: list[str] = []
    cursor = 0

    for range_start, range_end in protected:
        if range_end <= text_start:
            continue
        if range_start >= text_end:
            break

        rel_start = max(range_start, text_start) - text_start
        rel_end = min(range_end, text_end) - text_start

        if rel_start > cursor:
            out.append(transform(text[cursor:rel_start]))
        if rel_end > cursor:
            out.append(text[rel_start:rel_end])
            cursor = rel_end

    if cursor < len(text):
        out.append(transform(text[cursor:]))

    return "".join(out)


def fix_html_table_math_spaces(text: str) -> str:
    """Fix math delimiter spaces inside HTML ``<td>`` cells and normalize surrounding whitespace."""
    protected = _build_protected_ranges(text, include_inline_math=False)

    def _fix_cell(m: re.Match) -> str:
        open_tag = m.group(1)
        content = m.group(2)
        close_tag = m.group(3)
        content = _apply_transform_outside_protected_ranges(
            content,
            protected,
            lambda chunk: re.sub(
                r"(\$)\s{2,}",
                r"\1 ",
                re.sub(
                    r"\s{2,}(\$)",
                    r" \1",
                    _RE_INLINE_MATH.sub(
                        lambda im: (
                            f"${im.group(1).strip()}$" if im.group(1).strip() else im.group(0)
                        ),
                        chunk,
                    ),
                ),
            ),
            start_offset=m.start(2),
        )
        return f"{open_tag}{content}{close_tag}"

    return _RE_TD_INLINE_MATH.sub(_fix_cell, text)


def _normalize_footnote_definitions_preserving_state(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    in_notes = False
    notes_level: int | None = None
    notes_heading_re = re.compile(
        r"^#{1,6}\s*(参考文献|参考资料|参考书目|文献|引用|注释|脚注|notes?|references?|bibliography|works\s+cited|citations?)\b",
        re.IGNORECASE,
    )
    notes_heading_plain_re = re.compile(
        r"^(参考文献|参考资料|参考书目|文献|引用|注释|脚注|notes?|references?|bibliography|works\s+cited|citations?)\s*:?$",
        re.IGNORECASE,
    )
    last_note_index: int | None = None
    protected = _build_protected_ranges(text, include_inline_math=True)

    def line_overlaps(start: int, end: int) -> bool:
        for range_start, range_end in protected:
            if range_end <= start:
                continue
            if range_start >= end:
                break
            return True
        return False

    offset = 0
    for line in lines:
        line_start = offset
        line_end = offset + len(line)
        protected_line = line_overlaps(line_start, line_end)
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            run_len = 0
            while run_len < len(stripped) and stripped[run_len] == stripped[0]:
                run_len += 1
            if not in_fence:
                in_fence = True
                fence_char = stripped[0]
                fence_len = run_len
            elif stripped[0] == fence_char and run_len >= fence_len:
                in_fence = False
                fence_char = ""
                fence_len = 0
            out.append(line)
            offset = line_end
            continue

        if in_fence:
            out.append(line)
            offset = line_end
            continue

        heading_match = notes_heading_re.match(stripped)
        if heading_match:
            in_notes = True
            notes_level = len(stripped.split(" ", 1)[0])
            last_note_index = None
        elif notes_heading_plain_re.match(stripped):
            in_notes = True
            notes_level = None
            last_note_index = None
        elif re.match(r"^#{1,6}\s+", stripped):
            if in_notes:
                in_notes = False
                notes_level = None
                last_note_index = None

        match = re.match(r"^\[\^([0-9]+)\]\s+", line)
        if match:
            if protected_line:
                out.append(line)
            else:
                out.append(re.sub(r"^\[\^([0-9]+)\]\s+", r"[^\1]: ", line))
            offset = line_end
            continue

        if in_notes:
            list_match = re.match(r"^\s*(\d{1,4})[.)]\s+", line)
            if list_match:
                if protected_line:
                    out.append(line)
                    last_note_index = len(out) - 1
                else:
                    number = list_match.group(1)
                    line_ending = "\n" if line.endswith("\n") else ""
                    rest = line[list_match.end() :].rstrip("\r\n")
                    out.append(f"[^{number}]: {rest}{line_ending}")
                    last_note_index = len(out) - 1
                offset = line_end
                continue
            if last_note_index is not None:
                if line.strip() == "":
                    out.append(line)
                    last_note_index = None
                    offset = line_end
                    continue
                if line.startswith((" ", "\t")):
                    line_ending = "\n" if out[last_note_index].endswith("\n") else ""
                    out[last_note_index] = (
                        out[last_note_index].rstrip("\r\n") + f" {line.strip()}{line_ending}"
                    )
                    offset = line_end
                    continue
            if notes_level is None and stripped and not notes_heading_plain_re.match(stripped):
                in_notes = False

        if not protected_line:
            line = re.sub(r"(?<!\^)\[(\d{1,4})\]", r"[^\1]", line)
        out.append(line)
        offset = line_end

    return "".join(out)


def fix_markdown(text: str, level: str) -> str:
    if level == "off":
        return text

    # PaddleOCR-compatible cleanup (idempotent, safe for all OCR backends)
    text = fix_nested_mailto(text)
    text = fix_non_math_in_delimiters(text)
    text = fix_math_delimiter_spaces(text)
    text = fix_html_table_math_spaces(text)

    ref_processor = ReferenceProcessor()
    link_processor = LinkProcessor()
    pseudo_processor = PseudocodeProcessor()
    title_processor = TitleProcessor()

    text = merge_paragraphs(text)
    text = link_processor.fix_links(text)
    text = ref_processor.fix_references(text)
    text = pseudo_processor.wrap_pseudocode_blocks(text)

    if level == "aggressive":
        text = title_processor.fix_titles(text)

    try:
        from deepresearch_flow.paper.web.markdown import (
            normalize_fenced_code_blocks,
            normalize_mermaid_blocks,
            normalize_unbalanced_fences,
        )
    except ImportError:
        return text

    text = normalize_fenced_code_blocks(text)
    text = normalize_mermaid_blocks(text)
    text = normalize_unbalanced_fences(text)
    text = _normalize_footnote_definitions_preserving_state(text)

    return text
