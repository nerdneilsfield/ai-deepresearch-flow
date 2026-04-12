"""Markdown protection and restoration using placeholders."""

from __future__ import annotations

import re
from typing import List, Optional

from deepresearch_flow.translator.config import TranslateConfig
from deepresearch_flow.translator.placeholder import PlaceHolderStore


class MarkdownProtector:
    BLOCK_HTML_TAGS = (
        "address|article|aside|blockquote|body|caption|center|col|colgroup|dd|details|dialog|div|dl|dt|fieldset|"
        "figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|legend|li|link|main|menu|nav|"
        "noframes|ol|optgroup|option|p|param|section|summary|table|tbody|td|tfoot|th|thead|title|tr|ul|video|audio|canvas"
    )
    VOID_HTML_TAGS = (
        "area|base|br|col|embed|hr|img|input|keygen|link|meta|param|source|track|wbr"
    )

    def protect(self, text: str, cfg: TranslateConfig, store: PlaceHolderStore) -> str:
        store.record_source_placeholder_like_tokens(text)
        stage1 = self._partition_by_blocks(text, cfg, store)
        stage2 = self._freeze_inline(stage1, cfg, store)
        return stage2

    def unprotect(self, text: str, store: PlaceHolderStore) -> str:
        return store.restore_all_checked(text)

    @staticmethod
    def _is_blank(line: str) -> bool:
        return len(line.strip()) == 0

    @staticmethod
    def _line_starts_with_fence(line: str) -> tuple[str, int] | None:
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if not match:
            return None
        fence = match.group(1)
        return fence[0], len(fence)

    @staticmethod
    def _line_is_fence_close(line: str, fence_char: str, fence_len: int) -> bool:
        return (
            re.match(rf"^\s*{re.escape(fence_char)}{{{fence_len},}}\s*$", line)
            is not None
        )

    @staticmethod
    def _scan_paren_math_end(text: str, start: int) -> int | None:
        i = start + 2
        n = len(text)
        while i < n - 1:
            if text[i] == "\\" and text[i + 1] == ")":
                backslashes = 0
                j = i - 1
                while j >= start + 2 and text[j] == "\\":
                    backslashes += 1
                    j -= 1
                if backslashes % 2 == 0:
                    return i + 2
            i += 1
        return None

    @staticmethod
    def _freeze_paren_math(text: str, store: PlaceHolderStore) -> str:
        out: List[str] = []
        i = 0
        n = len(text)
        while i < n:
            start = text.find(r"\(", i)
            if start < 0:
                out.append(text[i:])
                break
            out.append(text[i:start])
            end = MarkdownProtector._scan_paren_math_end(text, start)
            if end is None:
                out.append(text[start:])
                break
            out.append(store.add("MATH", text[start:end]))
            i = end
        return "".join(out)

    @staticmethod
    def _line_is_block_math_open(line: str) -> bool:
        return line.strip() == "$$"

    @staticmethod
    def _line_is_block_math_close(line: str) -> bool:
        return line.strip() == "$$"

    @staticmethod
    def _line_starts_html_codey(line: str) -> Optional[str]:
        match = re.search(r"<(pre|code|script|style)(\s|>)", line, flags=re.IGNORECASE)
        return match.group(1).lower() if match else None

    @staticmethod
    def _line_ends_html(tag: str, line: str) -> bool:
        return re.search(rf"</{tag}\s*>", line, flags=re.IGNORECASE) is not None

    @staticmethod
    def _looks_like_table_header(line: str) -> bool:
        return "|" in line

    @staticmethod
    def _looks_like_table_delim(line: str) -> bool:
        return (
            re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line)
            is not None
        )

    @staticmethod
    def _line_starts_block_html_open(line: str) -> str | None:
        s = line.lstrip()
        if s.startswith("<!--"):
            return "__comment__"
        if s.startswith("<![CDATA["):
            return "__cdata__"
        if s.startswith("<?"):
            return "__pi__"
        match = re.match(
            rf"^<(?P<tag>{MarkdownProtector.BLOCK_HTML_TAGS})\b", s, flags=re.IGNORECASE
        )
        if match:
            return match.group("tag").lower()
        return None

    @staticmethod
    def _scan_until_html_block_end(lines: List[str], start: int, tag: str) -> int:
        n = len(lines)
        if tag == "__comment__":
            idx = start
            while idx < n and "-->" not in lines[idx]:
                idx += 1
            return min(idx, n - 1)
        if tag == "__cdata__":
            idx = start
            while idx < n and "]]>" not in lines[idx]:
                idx += 1
            return min(idx, n - 1)
        if tag == "__pi__":
            idx = start
            while idx < n and "?>" not in lines[idx]:
                idx += 1
            return min(idx, n - 1)

        if re.match(rf"^(?:{MarkdownProtector.VOID_HTML_TAGS})$", tag, flags=re.IGNORECASE):
            return start

        open_pat = re.compile(rf"<{tag}\b(?![^>]*?/>)", re.IGNORECASE)
        close_pat = re.compile(rf"</{tag}\s*>", re.IGNORECASE)
        depth = 0
        idx = start
        while idx < n:
            depth += len(open_pat.findall(lines[idx]))
            depth -= len(close_pat.findall(lines[idx]))
            if depth <= 0 and idx >= start:
                return idx
            idx += 1
        return n - 1

    @staticmethod
    def _partition_by_blocks(
        text: str, cfg: TranslateConfig, store: PlaceHolderStore
    ) -> str:
        lines = text.splitlines(keepends=True)
        out: List[str] = []
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]

            fence = MarkdownProtector._line_starts_with_fence(line)
            if fence:
                fence_char, fence_len = fence
                j = i + 1
                while j < n and not MarkdownProtector._line_is_fence_close(
                    lines[j], fence_char, fence_len
                ):
                    j += 1
                if j < n:
                    block = "".join(lines[i : j + 1])
                    placeholder = store.add("CODEFENCE", block)
                    out.append(placeholder + ("\n" if block.endswith("\n") else ""))
                    i = j + 1
                    continue
                block = "".join(lines[i:])
                placeholder = store.add("CODEFENCE", block)
                out.append(placeholder)
                break

            tag = MarkdownProtector._line_starts_html_codey(line)
            if tag:
                j = i
                while j < n and not MarkdownProtector._line_ends_html(tag, lines[j]):
                    j += 1
                if j < n:
                    block = "".join(lines[i : j + 1])
                    placeholder = store.add("HTMLBLOCK", block)
                    out.append(placeholder + ("\n" if block.endswith("\n") else ""))
                    i = j + 1
                    continue
                block = "".join(lines[i:])
                placeholder = store.add("HTMLBLOCK", block)
                out.append(placeholder)
                break

            tag_block = MarkdownProtector._line_starts_block_html_open(line)
            if tag_block:
                j = MarkdownProtector._scan_until_html_block_end(lines, i, tag_block)
                block = "".join(lines[i : j + 1])
                placeholder = store.add("HTMLBLOCK", block)
                out.append(placeholder + ("\n" if block.endswith("\n") else ""))
                i = j + 1
                continue

            if MarkdownProtector._line_is_block_math_open(line):
                j = i + 1
                while j < n and not MarkdownProtector._line_is_block_math_close(lines[j]):
                    j += 1
                if j < n:
                    block = "".join(lines[i : j + 1])
                    placeholder = store.add("MATHBLOCK", block)
                    out.append(placeholder + ("\n" if block.endswith("\n") else ""))
                    i = j + 1
                    continue
                block = "".join(lines[i:])
                placeholder = store.add("MATHBLOCK", block)
                out.append(placeholder)
                break

            if not cfg.translate_tables:
                if (
                    i + 1 < n
                    and MarkdownProtector._looks_like_table_header(line)
                    and MarkdownProtector._looks_like_table_delim(lines[i + 1])
                ):
                    j = i + 2
                    while (
                        j < n
                        and ("|" in lines[j] or MarkdownProtector._looks_like_table_delim(lines[j]))
                        and not MarkdownProtector._is_blank(lines[j])
                    ):
                        j += 1
                    block = "".join(lines[i:j])
                    placeholder = store.add("TABLE", block)
                    out.append(placeholder + ("\n" if block.endswith("\n") else ""))
                    i = j
                    continue

            if re.match(r"^\[\^[^\]]+\]:", line):
                j = i + 1
                while j < n and (
                    re.match(r"^\s{4,}", lines[j]) or MarkdownProtector._is_blank(lines[j])
                ):
                    j += 1
                block = "".join(lines[i:j])
                placeholder = store.add("FOOTDEF", block)
                out.append(placeholder + ("\n" if block.endswith("\n") else ""))
                i = j
                continue

            if re.match(r"^( {4}|\t)", line):
                j = i + 1
                while j < n and re.match(r"^( {4}|\t)", lines[j]):
                    j += 1
                block = "".join(lines[i:j])
                placeholder = store.add("INDENTCODE", block)
                out.append(placeholder + ("\n" if block.endswith("\n") else ""))
                i = j
                continue

            out.append(line)
            i += 1

        return "".join(out)

    @staticmethod
    def _scan_inline_bracket(text: str, start: int, open_char: str, close_char: str) -> int | None:
        i = start + 1
        n = len(text)
        while i < n:
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == close_char:
                return i
            i += 1
        return None

    @staticmethod
    def _scan_balanced_parens(text: str, start: int) -> int | None:
        depth = 1
        i = start + 1
        n = len(text)
        while i < n:
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return None

    @staticmethod
    def _freeze_inline_links_and_images(
        text: str, cfg: TranslateConfig, store: PlaceHolderStore
    ) -> str:
        out: List[str] = []
        i = 0
        n = len(text)
        while i < n:
            is_image = text[i] == "!" and i + 1 < n and text[i + 1] == "["
            is_link = text[i] == "["
            if not is_image and not is_link:
                out.append(text[i])
                i += 1
                continue

            start = i
            bracket_start = i + 1 if is_image else i
            bracket_end = MarkdownProtector._scan_inline_bracket(text, bracket_start, "[", "]")
            if bracket_end is None or bracket_end + 1 >= n or text[bracket_end + 1] != "(":
                out.append(text[i])
                i += 1
                continue
            paren_end = MarkdownProtector._scan_balanced_parens(text, bracket_end + 1)
            if paren_end is None:
                out.append(text[i])
                i += 1
                continue

            full = text[start : paren_end + 1]
            if is_image:
                if not cfg.translate_image_alt:
                    out.append(store.add("IMAGE", full))
                else:
                    head = "!["
                    alt = text[bracket_start + 1 : bracket_end]
                    tail = text[bracket_end + 1 : paren_end + 1]
                    out.append(f"{head}{alt}]{store.add('IMGURL', tail)}")
            else:
                if not cfg.translate_links_text:
                    out.append(store.add("LINK", full))
                else:
                    head = "["
                    label = text[bracket_start + 1 : bracket_end]
                    tail = text[bracket_end + 1 : paren_end + 1]
                    out.append(f"{head}{label}]{store.add('LINKURL', tail)}")
            i = paren_end + 1
        return "".join(out)

    @staticmethod
    def _freeze_inline(text: str, cfg: TranslateConfig, store: PlaceHolderStore) -> str:
        s = text

        def repl_link_def(match: re.Match) -> str:
            return store.add("LINKDEF", match.group(0))

        s = re.sub(r"^\s*\[[^\]]+\]:\s*\S+.*$", repl_link_def, s, flags=re.MULTILINE)

        inline_code_pattern = re.compile(r"(?<!`)(`+)([^`\n]+?)\1(?!`)")
        s = inline_code_pattern.sub(lambda m: store.add("CODE", m.group(0)), s)

        s = MarkdownProtector._freeze_paren_math(s, store)

        s = MarkdownProtector._freeze_inline_links_and_images(s, cfg, store)

        ref_link_pattern = re.compile(r"\[(?:[^\]\\]|\\.)*?\]\[[^\]]+\]")
        s = ref_link_pattern.sub(lambda m: store.add("REFLINK", m.group(0)), s)

        autolink_pattern = re.compile(r"<(?:https?://|mailto:)[^>]+>")
        s = autolink_pattern.sub(lambda m: store.add("AUTOLINK", m.group(0)), s)

        url_pattern = re.compile(r"(https?://[^ \n]+)")
        s = url_pattern.sub(lambda m: store.add("URL", m.group(0)), s)

        block_math_pattern = re.compile(r"\$\$[\s\S]+?\$\$")
        s = block_math_pattern.sub(lambda m: store.add("MATHBLOCK", m.group(0)), s)

        block_math_bracket_pattern = re.compile(r"\\\[[\s\S]+?\\\]")
        s = block_math_bracket_pattern.sub(lambda m: store.add("MATHBLOCK", m.group(0)), s)

        inline_math_pattern = re.compile(r"\$(?!\s)([^$\n]+?)\$(?!\$)")
        s = inline_math_pattern.sub(lambda m: store.add("MATH", m.group(0)), s)

        footref_pattern = re.compile(r"\[\^[^\]]+\]")
        s = footref_pattern.sub(lambda m: store.add("FOOTREF", m.group(0)), s)

        inline_html_pattern = re.compile(r"<[A-Za-z][^>]*?>.*?</[A-Za-z][^>]*?>", re.DOTALL)
        s = inline_html_pattern.sub(lambda m: store.add("HTMLINLINE", m.group(0)), s)

        return s
