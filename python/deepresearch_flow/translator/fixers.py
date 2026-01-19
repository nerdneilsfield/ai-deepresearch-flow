"""OCR markdown repair utilities."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional


class ReferenceProcessor:
    def __init__(self) -> None:
        self._patterns = {
            "reference_def": re.compile(
                r"^\[(\d+)\]((?:(?!\[\d+\])[^\n])*)\n(?=^\[\d+\]|$)",
                re.MULTILINE,
            ),
            "reference_range": re.compile(r"\[(\d+)\-(\d+)\]"),
            "reference_multi": re.compile(r"\[(\d+(?:,\s*\d+)*)\]"),
            "reference_single": re.compile(r"\[(\d+)\]"),
        }

    def fix_references(self, text: str) -> str:
        for match in re.findall(self._patterns["reference_def"], text):
            original = f"[{match[0]}] {match[1].strip()}"
            replacement = f"[^{match[0]}]: {match[1].strip()}\n"
            text = text.replace(original, replacement)

        for match in re.findall(self._patterns["reference_range"], text):
            original = f"[{match[0]}-{match[1]}]"
            expanded = " ".join(
                f"[^{i}]" for i in range(int(match[0]), int(match[1]) + 1)
            )
            text = text.replace(original, expanded)

        for match in re.findall(self._patterns["reference_multi"], text):
            original = f"[{match}]"
            numbers = [n.strip() for n in match.split(",")]
            expanded = " ".join(f"[^{n}]" for n in numbers)
            text = text.replace(original, expanded)

        for match in re.findall(self._patterns["reference_single"], text):
            original = f"[{match}]"
            replacement = f"[^{match}]"
            if original in text and f"[^{match}]" not in text.replace(replacement, ""):
                text = text.replace(original, replacement)

        return text


class LinkProcessor:
    def __init__(self) -> None:
        self._patterns = {
            "url": re.compile(
                r"(?<!<)(?<!]\()(?:(?<=^)|(?<=\s)|(?<=[\(\[{\"“]))"
                r"(https?://[^\s\)\]\}>]+)"
                r"(?=[\s\)\]\}>.,!?;:，。！？；：]|$)"
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
        def bracket_urls(value: str) -> str:
            def repl(match: re.Match) -> str:
                url = match.group(1)
                if len(url) > 1 and url[-1] in ".,!?;:，。！？；：" and url[-2].isalnum():
                    return f"<{url[:-1]}>{url[-1]}"
                return f"<{url}>"

            return self._patterns["url"].sub(repl, value)

        def bracket_emails(value: str) -> str:
            return self._patterns["email"].sub(r"<mailto:\1>", value)

        def bracket_phones(value: str) -> str:
            return self._patterns["phone"].sub(lambda m: f"<tel:{m.group(1)}>", value)

        text = bracket_urls(text)
        text = bracket_emails(text)
        text = bracket_phones(text)
        return text


class PseudocodeProcessor:
    def __init__(self) -> None:
        self._header_pattern = re.compile(
            r"^\s*\*?\*?\s*(Algorithm|算法)\s+([A-Za-z0-9.-]+)?\*?\*?\s*(.*)$",
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
        text = re.sub(r"<\s*sub\s*>\s*(.*?)\s*<\s*/\s*sub\s*>", lambda m: "_" + re.sub(r"\*", "", m.group(1)), text, flags=re.I)
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
    return (
        re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line)
        is not None
    )


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
            if j + 1 < n and _looks_like_table_header(peek) and _looks_like_table_delim(lines[j + 1]):
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

        blocks = blocks[:idx] + [Block(kind="text", content=merged_text), middle] + blocks[idx + 3 :]
        idx += 1

    return blocks


def merge_paragraphs(text: str) -> str:
    blocks = _parse_blocks(text)
    merged = _merge_blocks(blocks)
    return "".join(block.content for block in merged)


def fix_markdown(text: str, level: str) -> str:
    if level == "off":
        return text

    ref_processor = ReferenceProcessor()
    link_processor = LinkProcessor()
    pseudo_processor = PseudocodeProcessor()
    title_processor = TitleProcessor()

    text = merge_paragraphs(text)
    text = ref_processor.fix_references(text)
    text = link_processor.fix_links(text)
    text = pseudo_processor.wrap_pseudocode_blocks(text)

    if level == "aggressive":
        text = title_processor.fix_titles(text)

    try:
        from deepresearch_flow.paper.web.markdown import (
            normalize_fenced_code_blocks,
            normalize_footnote_definitions,
            normalize_mermaid_blocks,
            normalize_unbalanced_fences,
        )
    except Exception:
        return text

    text = normalize_fenced_code_blocks(text)
    text = normalize_mermaid_blocks(text)
    text = normalize_unbalanced_fences(text)
    text = normalize_footnote_definitions(text)

    return text
