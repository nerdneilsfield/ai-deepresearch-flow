"""Math formula validation and repair helpers."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import logging
import re
import atexit
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, Callable, Iterable

import httpx

from deepresearch_flow.paper.llm import backoff_delay, call_provider
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.paper.utils import parse_json, short_hash

try:
    from pylatexenc.latexwalker import LatexWalker, LatexWalkerError
except ImportError:  # pragma: no cover - dependency guard
    LatexWalker = None
    LatexWalkerError = Exception

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormulaSpan:
    start: int
    end: int
    delimiter: str
    content: str
    line: int
    context: str


@dataclass
class FormulaIssue:
    issue_id: str
    span: FormulaSpan
    errors: list[str]
    cleaned: str
    field_path: str | None
    item_index: int | None


@dataclass
class MathFixStats:
    formulas_total: int = 0
    formulas_invalid: int = 0
    formulas_cleaned: int = 0
    formulas_repaired: int = 0
    formulas_failed: int = 0


_KATEX_WARNED = False
_NODE_VALIDATOR: "NodeKatexValidator | None" = None
_NODE_KATEX_READY: bool | None = None


def require_pylatexenc() -> None:
    if LatexWalker is None:
        raise RuntimeError("pylatexenc is required for fix-math")


def _mask_regex(text: str, pattern: str, flags: int = 0) -> str:
    masked = list(text)
    for match in re.finditer(pattern, text, flags):
        for idx in range(match.start(), match.end()):
            masked[idx] = " "
    return "".join(masked)


def _mask_code(text: str) -> str:
    masked = _mask_regex(text, r"```[\s\S]*?```")
    masked = _mask_regex(masked, r"(?<!`)(`+)([^`\n]+?)\1(?!`)")
    return masked


def extract_math_spans(text: str, context_chars: int) -> list[FormulaSpan]:
    masked = _mask_code(text)
    spans: list[FormulaSpan] = []
    for match in re.finditer(r"(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$", masked):
        content = text[match.start() + 2 : match.end() - 2]
        line = text.count("\n", 0, match.start()) + 1
        context = text[max(0, match.start() - context_chars) : match.end() + context_chars]
        spans.append(
            FormulaSpan(
                start=match.start(),
                end=match.end(),
                delimiter="$$",
                content=content,
                line=line,
                context=context,
            )
        )

    block_ranges = [(span.start, span.end) for span in spans]
    inline_pattern = re.compile(r"(?<!\\)\$(?!\s|\$)([^$\n]+?)(?<!\\)\$(?!\$)")
    for match in inline_pattern.finditer(masked):
        if any(start <= match.start() < end for start, end in block_ranges):
            continue
        content = text[match.start() + 1 : match.end() - 1]
        line = text.count("\n", 0, match.start()) + 1
        context = text[max(0, match.start() - context_chars) : match.end() + context_chars]
        spans.append(
            FormulaSpan(
                start=match.start(),
                end=match.end(),
                delimiter="$",
                content=content,
                line=line,
                context=context,
            )
        )

    return sorted(spans, key=lambda span: span.start)


def cleanup_formula(text: str) -> str:
    cleaned = text.replace("\u00a0", " ").strip()
    cleaned = cleaned.replace("\r", "")
    cleaned = cleaned.replace("\text", "\\text").replace("\tfrac", "\\tfrac")
    cleaned = cleaned.replace("\t", "")
    cleaned = re.sub(r"\\(?=\d)", "", cleaned)
    cleaned = re.sub(r"\\n(?=[A-Za-z])", "", cleaned)
    cleaned = re.sub(r"\\\s+([A-Za-z])", r"\\\1", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"\\\(\s*cdot\s*\)", r"\\cdot", cleaned)
    cleaned = re.sub(r"\\\s*Max\b", r"\\max", cleaned)
    cleaned = re.sub(r"\\\s*Min\b", r"\\min", cleaned)
    cleaned = re.sub(r"\x08eta(?=[_\\^,\\s)])", r"\\beta", cleaned)
    cleaned = re.sub(r"\x08eta\b", r"\\beta", cleaned)
    cleaned = re.sub(r"\x08ar(?=[_\\{\\^\\s)])", r"\\bar", cleaned)
    cleaned = re.sub(r"\x08ar\b", r"\\bar", cleaned)
    cleaned = re.sub(r"\\\s+([A-Za-z]{2,})", r"\\text{\1}", cleaned)
    cleaned = re.sub(
        r"([A-Za-z0-9_{}\\]+)\^([A-Za-z0-9_{}]+)\^([A-Za-z0-9_{}]+)",
        r"({\1}^{\2})^{\3}",
        cleaned,
    )
    cleaned = re.sub(r"(\\right)\s*ceil\b", r"\\right\\rceil", cleaned)
    cleaned = re.sub(r"(\\left)\s*ceil\b", r"\\left\\lceil", cleaned)
    cleaned = re.sub(
        r"\\Big\s*{\\(lfloor|lceil|rfloor|rceil|langle|rangle)}",
        r"\\Big\\\1",
        cleaned,
    )
    cleaned = re.sub(r"\x08egin\b", r"\\begin", cleaned)
    cleaned = re.sub(r"\x08oldsymbol\b", r"\\boldsymbol", cleaned)
    cleaned = re.sub(r"\^''", r"^{''}", cleaned)
    cleaned = re.sub(r"\^'", r"^{\\prime}", cleaned)
    cleaned = re.sub(r"\^_", r"^{*}", cleaned)
    cleaned = re.sub(r"\\operatorname_\s*{", r"\\operatorname{", cleaned)
    cleaned = re.sub(r"_\s*{\\times}", r"\\times", cleaned)
    cleaned = re.sub(r"_\s*\\times\b", r"\\times", cleaned)
    cleaned = re.sub(r"\\arg\s+\\max\s*_\s*{", r"\\arg\\max_{", cleaned)
    cleaned = re.sub(r"\^\s*{\s*_?\s*}", "", cleaned)
    cleaned = re.sub(
        r"([A-Za-z0-9]+_\s*{[^}]+})\s*_\s*([A-Za-z])",
        r"\1 \2",
        cleaned,
    )
    cleaned = _collapse_spaced_text_commands(cleaned)
    cleaned = _split_text_with_math(cleaned)
    cleaned = _normalize_unknown_commands(cleaned)
    return cleaned


def _collapse_spaced_text(text: str) -> str:
    tokens = text.split()
    if not tokens:
        return text
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if len(tokens[i]) == 1:
            j = i
            while j < len(tokens) and len(tokens[j]) == 1:
                j += 1
            out.append("".join(tokens[i:j]))
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return " ".join(out)


def _collapse_spaced_text_commands(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        content = match.group(2)
        collapsed = _collapse_spaced_text(content)
        return f"\\{name}{{{collapsed}}}"

    return re.sub(r"\\(text|operatorname\*?)\s*{([^}]*)}", replace, text)


def _split_text_with_math(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        content = match.group(1)
        collapsed = _collapse_spaced_text(content)
        token = re.search(r"(\\times|\\frac|\\sum|\\prod|\\left|\\right|\^|_)", collapsed)
        if not token:
            return f"\\text{{{collapsed}}}"
        idx = token.start()
        prefix = collapsed[:idx].rstrip()
        suffix = collapsed[idx:].lstrip()
        if not prefix:
            return suffix
        return f"\\text{{{prefix}}} {suffix}"

    return re.sub(r"\\text\s*{([^}]*)}", replace, text)


_KNOWN_LATEX_COMMANDS = {
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "varepsilon",
    "zeta",
    "eta",
    "theta",
    "vartheta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "varphi",
    "chi",
    "psi",
    "omega",
    "Gamma",
    "Delta",
    "Theta",
    "Lambda",
    "Xi",
    "Pi",
    "Sigma",
    "Upsilon",
    "Phi",
    "Psi",
    "Omega",
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "arcsin",
    "arccos",
    "arctan",
    "sinh",
    "cosh",
    "tanh",
    "log",
    "ln",
    "exp",
    "min",
    "max",
    "argmin",
    "argmax",
    "sqrt",
    "frac",
    "cdot",
    "times",
    "left",
    "right",
    "lceil",
    "rceil",
    "langle",
    "rangle",
    "lvert",
    "rvert",
    "lVert",
    "rVert",
    "sum",
    "prod",
    "int",
    "lim",
    "infty",
    "partial",
    "nabla",
    "cdots",
    "ldots",
    "text",
    "mathrm",
    "mathbf",
    "mathit",
    "mathcal",
    "mathbb",
    "overline",
    "underline",
    "bar",
    "hat",
    "tilde",
    "vec",
}


def _normalize_unknown_commands(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        command = match.group(1)
        if command in _KNOWN_LATEX_COMMANDS or not command[:1].isupper():
            return match.group(0)
        return f"\\text{{{command}}}"

    return re.sub(r"\\([A-Za-z]+)", replace, text)


def _validate_pylatex(text: str) -> str | None:
    require_pylatexenc()
    try:
        walker = LatexWalker(text)
        walker.get_latex_nodes()
    except LatexWalkerError as exc:
        return str(exc)
    except Exception as exc:  # pragma: no cover - safety net
        return str(exc)
    return None


class NodeKatexValidator:
    def __init__(self, node_path: str, script_path: str) -> None:
        self._node_path = node_path
        self._script_path = script_path
        self._lock = threading.Lock()
        self._process = self._spawn()
        atexit.register(self.close)

    def _spawn(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [self._node_path, self._script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def _ensure_alive(self) -> None:
        if self._process.poll() is not None:
            self._process = self._spawn()

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()

    def validate(self, latex: str, display_mode: bool) -> str | None:
        with self._lock:
            self._ensure_alive()
            payload = {"latex": latex, "opts": {"displayMode": display_mode}}
            try:
                assert self._process.stdin is not None
                assert self._process.stdout is not None
                self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
                line = self._process.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                return f"katex validator IO error: {exc}"
            if not line:
                return "katex validator returned empty response"
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                return "katex validator returned invalid JSON"
            if response.get("ok") is True:
                return None
            return str(response.get("error") or "katex validation failed")


def _ensure_node_validator() -> NodeKatexValidator | None:
    global _KATEX_WARNED, _NODE_VALIDATOR, _NODE_KATEX_READY
    if _NODE_VALIDATOR is not None:
        return _NODE_VALIDATOR
    node_path = shutil.which("node")
    if not node_path:
        if not _KATEX_WARNED:
            logger.warning("node binary not found; skip KaTeX validation")
            _KATEX_WARNED = True
        return None
    if _NODE_KATEX_READY is None:
        try:
            result = subprocess.run(
                [node_path, "-e", "require('katex')"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _NODE_KATEX_READY = result.returncode == 0
        except OSError:
            _NODE_KATEX_READY = False
        if not _NODE_KATEX_READY:
            if not _KATEX_WARNED:
                logger.warning(
                    "katex npm package not available; skip KaTeX validation (node=%s)",
                    node_path,
                )
                _KATEX_WARNED = True
            return None
    script_path = str((Path(__file__).with_name("katex_check.js")).resolve())
    _NODE_VALIDATOR = NodeKatexValidator(node_path, script_path)
    return _NODE_VALIDATOR


def _validate_katex(text: str, display_mode: bool) -> str | None:
    validator = _ensure_node_validator()
    if validator is None:
        return None
    return validator.validate(text, display_mode)


def validate_formula(text: str, display_mode: bool) -> list[str]:
    errors: list[str] = []
    pylatex_error = _validate_pylatex(text)
    if pylatex_error:
        errors.append(pylatex_error)
    katex_error = _validate_katex(text, display_mode)
    if katex_error:
        errors.append(katex_error)
    return errors


def apply_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    if not replacements:
        return text
    updated = text
    for start, end, value in sorted(replacements, key=lambda item: item[0], reverse=True):
        updated = updated[:start] + value + updated[end:]
    return updated


def repair_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "latex": {"type": "string"},
                    },
                    "required": ["id", "latex"],
                },
            }
        },
        "required": ["items"],
    }


def build_repair_messages(issues: list[FormulaIssue]) -> list[dict[str, str]]:
    payload = [
        {
            "id": issue.issue_id,
            "delimiter": issue.span.delimiter,
            "latex": issue.span.content,
            "errors": issue.errors,
            "context": issue.span.context,
        }
        for issue in issues
    ]
    system = (
        "You repair LaTeX math expressions. Fix syntax errors only and keep the "
        "mathematical meaning unchanged. Return JSON with key 'items' and each "
        "item containing {\"id\", \"latex\"}. Output JSON only."
    )
    user = json.dumps({"items": payload}, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def iter_batches(items: list[FormulaIssue], batch_size: int) -> Iterable[list[FormulaIssue]]:
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]


def locate_json_field_start(
    raw_text: str,
    field_value: str,
    search_start: int,
) -> tuple[int, int]:
    needle = json.dumps(field_value, ensure_ascii=False)
    inner = needle[1:-1] if needle.startswith("\"") and needle.endswith("\"") else needle
    idx = raw_text.find(inner, search_start)
    if idx == -1:
        idx = raw_text.find(needle, search_start)
    if idx == -1:
        return 1, search_start
    line = raw_text.count("\n", 0, idx) + 1
    return line, idx + len(inner)


def strip_wrapping_delimiters(latex: str, delimiter: str) -> str:
    latex = latex.strip()
    if latex.startswith(delimiter) and latex.endswith(delimiter):
        return latex[len(delimiter) : -len(delimiter)].strip()
    return latex


def _parse_repairs(response: str) -> dict[str, str]:
    parsed = parse_json(response)
    items = parsed.get("items", [])
    repairs: dict[str, str] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            issue_id = item.get("id")
            latex = item.get("latex")
            if isinstance(issue_id, str) and isinstance(latex, str):
                repairs[issue_id] = latex
    return repairs


async def repair_batch(
    issues: list[FormulaIssue],
    provider,
    model_name: str,
    api_key: str | None,
    timeout: float,
    max_retries: int,
    client: httpx.AsyncClient,
) -> tuple[dict[str, str], str | None]:
    messages = build_repair_messages(issues)
    schema = repair_schema()
    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            response = await call_provider(
                provider,
                model_name,
                messages,
                schema,
                api_key,
                timeout,
                provider.structured_mode,
                client,
                max_tokens=provider.max_tokens,
            )
            repairs = _parse_repairs(response)
            return repairs, None
        except (ValueError, TypeError) as exc:
            last_error = f"parse_error: {exc}"
            if attempt < max_retries:
                await asyncio.sleep(backoff_delay(1.0, attempt + 1, 20.0))
                continue
            return {}, last_error
        except ProviderError as exc:
            last_error = str(exc)
            if exc.retryable and attempt < max_retries:
                await asyncio.sleep(backoff_delay(1.0, attempt + 1, 20.0))
                continue
            return {}, last_error
        except Exception as exc:  # pragma: no cover - safety net
            last_error = str(exc)
            if attempt < max_retries:
                await asyncio.sleep(backoff_delay(1.0, attempt + 1, 20.0))
                continue
            return {}, last_error
    return {}, last_error


async def fix_math_text(
    text: str,
    file_path: str,
    line_offset: int,
    field_path: str | None,
    item_index: int | None,
    provider,
    model_name: str,
    api_key: str | None,
    timeout: float,
    max_retries: int,
    batch_size: int,
    context_chars: int,
    client: httpx.AsyncClient,
    stats: MathFixStats,
    repair_enabled: bool = True,
    spans: list[FormulaSpan] | None = None,
    allowed_keys: set[tuple[int, str | None, int | None]] | None = None,
    progress_cb: Callable[[], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    replacements: list[tuple[int, int, str]] = []
    issues: list[FormulaIssue] = []
    if spans is None:
        spans = extract_math_spans(text, context_chars)
    if allowed_keys:
        filtered: list[FormulaSpan] = []
        for span in spans:
            line_no = line_offset + span.line - 1
            if (line_no, field_path, item_index) in allowed_keys:
                filtered.append(span)
        spans = filtered
    if not spans:
        return text, []
    stats.formulas_total += len(spans)
    file_id = short_hash(file_path)
    for idx, span in enumerate(spans):
        display_mode = span.delimiter == "$$"
        original = span.content
        errors = validate_formula(original, display_mode)
        cleaned = original
        if errors:
            candidate = cleanup_formula(original)
            if candidate != original:
                candidate_errors = validate_formula(candidate, display_mode)
                if not candidate_errors:
                    stats.formulas_cleaned += 1
                    wrapped = f"{span.delimiter}{candidate}{span.delimiter}"
                    replacements.append((span.start, span.end, wrapped))
                    if progress_cb:
                        progress_cb()
                    continue
                cleaned = candidate
                errors = candidate_errors

            stats.formulas_invalid += 1
            issue_id = f"{file_id}:{idx}"
            issues.append(
                FormulaIssue(
                    issue_id=issue_id,
                    span=span,
                    errors=errors,
                    cleaned=cleaned,
                    field_path=field_path,
                    item_index=item_index,
                )
            )
        if progress_cb:
            progress_cb()

    error_records: list[dict[str, Any]] = []
    if issues and repair_enabled:
        # Convert to list for parallel processing
        batches = list(iter_batches(issues, batch_size))
        
        # Parallel batch repair
        batch_results = await asyncio.gather(
            *[
                repair_batch(batch, provider, model_name, api_key, timeout, max_retries, client)
                for batch in batches
            ],
            return_exceptions=True,
        )
        
        # Process results
        for batch, result in zip(batches, batch_results):
            if isinstance(result, Exception):
                # Entire batch failed with exception
                error = str(result)
                for issue in batch:
                    stats.formulas_failed += 1
                    error_records.append({
                        "path": file_path,
                        "line": line_offset + issue.span.line - 1,
                        "delimiter": issue.span.delimiter,
                        "latex": issue.span.content,
                        "errors": issue.errors + [f"batch_exception: {error}"],
                        "field_path": issue.field_path,
                        "item_index": issue.item_index,
                    })
                continue
            
            repairs, error = result
            
            if error:
                for issue in batch:
                    stats.formulas_failed += 1
                    error_records.append(
                        {
                            "path": file_path,
                            "line": line_offset + issue.span.line - 1,
                            "delimiter": issue.span.delimiter,
                            "latex": issue.span.content,
                            "errors": issue.errors + [f"llm_error: {error}"],
                            "field_path": issue.field_path,
                            "item_index": issue.item_index,
                        }
                    )
                continue

            for issue in batch:
                repaired = repairs.get(issue.issue_id)
                if not repaired:
                    stats.formulas_failed += 1
                    error_records.append(
                        {
                            "path": file_path,
                            "line": line_offset + issue.span.line - 1,
                            "delimiter": issue.span.delimiter,
                            "latex": issue.span.content,
                            "errors": issue.errors + ["llm_missing_output"],
                            "field_path": issue.field_path,
                            "item_index": issue.item_index,
                        }
                    )
                    continue
                repaired = strip_wrapping_delimiters(repaired, issue.span.delimiter)
                cleaned = cleanup_formula(repaired)
                errors = validate_formula(cleaned, issue.span.delimiter == "$$")
                if errors:
                    stats.formulas_failed += 1
                    error_records.append(
                        {
                            "path": file_path,
                            "line": line_offset + issue.span.line - 1,
                            "delimiter": issue.span.delimiter,
                            "latex": issue.span.content,
                            "errors": errors,
                            "field_path": issue.field_path,
                            "item_index": issue.item_index,
                        }
                    )
                    continue
                stats.formulas_repaired += 1
                wrapped = f"{issue.span.delimiter}{cleaned}{issue.span.delimiter}"
                replacements.append((issue.span.start, issue.span.end, wrapped))
    elif issues:
        for issue in issues:
            stats.formulas_failed += 1
            error_records.append(
                {
                    "path": file_path,
                    "line": line_offset + issue.span.line - 1,
                    "delimiter": issue.span.delimiter,
                    "latex": issue.span.content,
                    "errors": issue.errors + ["validation_only"],
                    "field_path": issue.field_path,
                    "item_index": issue.item_index,
                }
            )

    updated = apply_replacements(text, replacements)
    return updated, error_records
