"""Mermaid validation and repair helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Iterable

import httpx

from deepresearch_flow.paper.llm import backoff_delay, call_provider
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.paper.utils import parse_json, short_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MermaidSpan:
    start: int
    end: int
    content: str
    line: int
    context: str


@dataclass
class MermaidIssue:
    issue_id: str
    span: MermaidSpan
    errors: list[str]
    field_path: str | None
    item_index: int | None


@dataclass
class DiagramTask:
    """Global diagram task for parallel processing."""
    file_path: Path
    file_line_offset: int
    field_path: str | None
    item_index: int | None
    span: MermaidSpan
    issue: MermaidIssue | None


@dataclass
class MermaidFixStats:
    diagrams_total: int = 0
    diagrams_invalid: int = 0
    diagrams_repaired: int = 0
    diagrams_failed: int = 0


_MMDC_PATH: str | None = None


def require_mmdc() -> None:
    global _MMDC_PATH
    if _MMDC_PATH:
        return
    local_mmdc = Path.cwd() / "node_modules" / ".bin" / "mmdc"
    if local_mmdc.exists():
        _MMDC_PATH = str(local_mmdc)
        return
    _MMDC_PATH = shutil.which("mmdc")
    if not _MMDC_PATH:
        raise RuntimeError("mmdc (mermaid-cli) not found; install with npm i -g @mermaid-js/mermaid-cli")


def extract_mermaid_spans(text: str, context_chars: int) -> list[MermaidSpan]:
    spans: list[MermaidSpan] = []
    pattern = re.compile(r"```\s*mermaid\s*\n([\s\S]*?)```", re.IGNORECASE)
    for match in pattern.finditer(text):
        content = match.group(1)
        content_start = match.start(1)
        content_end = match.end(1)
        line = text.count("\n", 0, content_start) + 1
        context = text[max(0, match.start() - context_chars) : match.end() + context_chars]
        spans.append(
            MermaidSpan(
                start=content_start,
                end=content_end,
                content=content,
                line=line,
                context=context,
            )
        )
    return spans


def cleanup_mermaid(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    cleaned = cleaned.replace("\u2028", "\n").replace("\u2029", "\n")
    cleaned = cleaned.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
    cleaned = _expand_escaped_newlines(cleaned)
    cleaned = _normalize_mermaid_lines(cleaned)
    cleaned = _normalize_subgraph_lines(cleaned)
    cleaned = _repair_edge_labels(cleaned)
    cleaned = _repair_missing_label_arrows(cleaned)
    cleaned = _normalize_label_linebreaks(cleaned)
    cleaned = _normalize_cylinder_labels(cleaned)
    cleaned = _wrap_html_labels(cleaned)
    cleaned = _close_unbalanced_labels(cleaned)
    cleaned = _split_compacted_statements(cleaned)
    cleaned = _split_chained_edges(cleaned)
    cleaned = _repair_dot_label_edges(cleaned)
    cleaned = _expand_multi_source_edges(cleaned)
    cleaned = _prefix_orphan_edges(cleaned)
    cleaned = _dedupe_subgraph_ids(cleaned)
    return cleaned


def _expand_escaped_newlines(text: str) -> str:
    out: list[str] = []
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "[({":
            depth += 1
        elif ch in "])}":
            depth = max(0, depth - 1)
        if ch == "\\" and i + 1 < len(text) and text[i + 1] == "n":
            out.append("<br/>" if depth > 0 else "\n")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _normalize_mermaid_lines(text: str) -> str:
    out_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            out_lines.append("")
            continue
        if line.startswith("%%"):
            out_lines.append(line)
            continue
        if "%%" in line:
            code, comment = line.split("%%", 1)
            code = code.strip()
            if code:
                out_lines.extend(_split_statements(code))
            comment = comment.strip()
            if comment:
                out_lines.append(f"%% {comment}")
            continue
        line = _split_statements(line)
        out_lines.extend(line)
    return "\n".join(out_lines)


def _split_statements(line: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in line:
        if ch in "[({":
            depth += 1
        elif ch in "])}":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts or [line]


def _normalize_subgraph_lines(text: str) -> str:
    lines = text.splitlines()
    normalized: list[str] = []
    counter = 0
    for line in lines:
        match = re.match(r"\s*subgraph\s+(.+)", line)
        if not match:
            normalized.append(line)
            continue
        rest = match.group(1).strip()
        label_match = re.match(r"(.+?)\s*\[(.+)\]\s*$", rest)
        if label_match:
            counter += 1
            label = label_match.group(2).replace("[", "(").replace("]", ")").strip()
            label = _quote_label_text(label)
            sub_id = f"subgraph_{counter}"
            normalized.append(f"subgraph {sub_id} [{label}]")
            continue
        counter += 1
        label = rest.replace("[", "(").replace("]", ")").strip()
        label = _quote_label_text(label)
        sub_id = f"subgraph_{counter}"
        normalized.append(f"subgraph {sub_id} [{label}]")
    return "\n".join(normalized)


def _normalize_label_linebreaks(text: str) -> str:
    def fix_block(block: str) -> str:
        if "-->" in block or "-.->" in block or "==>" in block or "\nsubgraph" in block or "\nend" in block:
            return block
        return block.replace("\n", "<br/>")

    return re.sub(
        r"\[[^\]]*\]",
        lambda match: fix_block(match.group(0)),
        text,
    )


def _repair_edge_labels(text: str) -> str:
    return re.sub(r"-->\s*\[([^\]]+)\]\s*", r"-->|\1| ", text)


def _repair_missing_label_arrows(text: str) -> str:
    return re.sub(r"--\s*([^|>]+)\|\s*", r"-->|\1| ", text)


def _split_compacted_statements(text: str) -> str:
    token_start = r"[A-Za-z0-9_\u4e00-\u9fff]"
    out = text
    out = re.sub(
        r"^(\s*(?:graph|flowchart)\s+[A-Za-z]{2})\s*(?=\S)",
        r"\1\n",
        out,
        flags=re.MULTILINE,
    )
    out = re.sub(
        rf"^(\s*(?:graph|flowchart)\s+[A-Za-z]{{2}})(?={token_start})",
        r"\1\n",
        out,
        flags=re.MULTILINE,
    )
    out = re.sub(rf"([)\]}}])\s*(?={token_start}+\s*-->)", r"\1\n", out)
    out = re.sub(rf"([)\]}}])\s*(?={token_start}+\s*[\[\(\{{])", r"\1\n", out)
    out = re.sub(rf"([)\]}}])(?={token_start})", r"\1\n", out)
    return out


def _prefix_orphan_edges(text: str) -> str:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if re.match(r"^\s*(-->|-\.-|==>)", line):
            lines[idx] = f"Start {line}"
    return "\n".join(lines)


def _normalize_cylinder_labels(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if "[(" in line:
            if ")]" in line:
                line = line.replace("[(", '["(').replace(")]", ')"]')
            else:
                def fix_label(match: re.Match[str]) -> str:
                    inner = match.group(1).strip()
                    wrapped = f"({inner}" if ")" in inner else f"({inner})"
                    return f'[\"{wrapped}\"]'

                line = re.sub(r"\[\(([^]]+)\]", fix_label, line)
        lines.append(line)
    return "\n".join(lines)


def _wrap_html_labels(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        inner = match.group(1).replace('"', "'")
        return f'["{inner}"]'

    return re.sub(
        r"\[([^\]]*<br\s*/?>[^\]]*)\]",
        repl,
        text,
    )


def _close_unbalanced_labels(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("%%"):
            lines.append(line)
            continue
        opens = line.count("[")
        closes = line.count("]")
        if opens > closes:
            line = line + ("]" * (opens - closes))
        lines.append(line)
    return "\n".join(lines)


def _quote_label_text(label: str) -> str:
    stripped = label.strip()
    if stripped.startswith(('"', "'")) and stripped.endswith(('"', "'")):
        return stripped
    return f'"{stripped.replace(chr(34), chr(39))}"'


def _split_chained_edges(text: str) -> str:
    out: list[str] = []
    arrows = ("-->", "-.->", "==>")
    for line in text.splitlines():
        if line.startswith("%%") or "subgraph" in line or line.strip() == "end":
            out.append(line)
            continue
        segments: list[str] = []
        tokens: list[str] = []
        buf: list[str] = []
        depth = 0
        i = 0
        while i < len(line):
            ch = line[i]
            if ch in "[({":
                depth += 1
            elif ch in "])}":
                depth = max(0, depth - 1)
            if depth == 0:
                matched = None
                for arrow in arrows:
                    if line.startswith(arrow, i):
                        matched = arrow
                        break
                if matched:
                    segments.append("".join(buf).strip())
                    tokens.append(matched)
                    buf = []
                    i += len(matched)
                    continue
            buf.append(ch)
            i += 1
        tail = "".join(buf).strip()
        if tail:
            segments.append(tail)
        if len(segments) <= 2:
            out.append(line)
            continue
        for idx, arrow in enumerate(tokens):
            left = segments[idx]
            right = segments[idx + 1]
            if left and right:
                out.append(f"{left} {arrow} {right}")
    return "\n".join(out)


def _repair_dot_label_edges(text: str) -> str:
    def fix_line(line: str) -> str:
        return re.sub(r"-\.\s*([^|>]+?)\s*\.-\s*>", r"-.->|\1|", line)

    return "\n".join(fix_line(line) for line in text.splitlines())


def _expand_multi_source_edges(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    pattern = re.compile(
        r"^(?P<indent>\s*)(?P<left>[A-Za-z0-9_]+(?:\s*&\s*[A-Za-z0-9_]+)+)\s*-->\s*(?P<right>.+)$"
    )
    for line in lines:
        match = pattern.match(line)
        if not match:
            out.append(line)
            continue
        indent = match.group("indent")
        right = match.group("right").strip()
        for node in match.group("left").split("&"):
            out.append(f"{indent}{node.strip()} --> {right}")
    return "\n".join(out)


def _dedupe_subgraph_ids(text: str) -> str:
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = re.match(r"\s*subgraph\s+([A-Za-z0-9_]+)\b", line)
        if not match:
            idx += 1
            continue
        sub_id = match.group(1)
        end_idx = idx + 1
        while end_idx < len(lines) and not re.match(r"\s*end\b", lines[end_idx]):
            end_idx += 1
        conflict = any(re.match(rf"\s*{re.escape(sub_id)}\b", ln) for ln in lines[idx + 1 : end_idx])
        if conflict:
            new_id = f"{sub_id}_group"
            lines[idx] = line.replace(sub_id, new_id, 1)
        idx = end_idx + 1
    return "\n".join(lines)


def _mermaid_temp_dir() -> Path:
    base_dir = Path("/tmp/mermaid")
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def validate_mermaid(mmd_text: str) -> str | None:
    require_mmdc()
    base_dir = _mermaid_temp_dir()
    with tempfile.TemporaryDirectory(dir=base_dir, prefix="mmdc-") as td:
        work_dir = Path(td)
        in_file = work_dir / "diagram.mmd"
        out_file = work_dir / "diagram.svg"
        in_file.write_text(mmd_text, encoding="utf-8")
        cmd = [
            _MMDC_PATH or "mmdc",
            "-i",
            str(in_file),
            "-o",
            str(out_file),
            "--quiet",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
            return None
        msg = (proc.stderr or "") + "\n" + (proc.stdout or "")
        msg = msg.strip() or f"mmdc failed with code {proc.returncode}"
        return msg


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
                        "mermaid": {"type": "string"},
                    },
                    "required": ["id", "mermaid"],
                },
            }
        },
        "required": ["items"],
    }


def build_repair_messages(issues: list[MermaidIssue]) -> list[dict[str, str]]:
    payload = [
        {
            "id": issue.issue_id,
            "mermaid": issue.span.content,
            "errors": issue.errors,
            "context": issue.span.context,
        }
        for issue in issues
    ]
    system = (
        "You repair Mermaid diagrams. Fix syntax errors only and keep the "
        "meaning unchanged. Return JSON with key 'items' and each item "
        "containing {\"id\", \"mermaid\"}. Output JSON only.\n\n"
        "Use this minimal safe subset for all repaired Mermaid output:\n"
        "- Only use: graph TD\n"
        "- Node IDs: ASCII letters/digits/underscore only\n"
        "- Node labels: id[\"中文...\"]\n"
        "- Line breaks in labels: use <br/> only\n"
        "- Subgraphs: use subgraph sgN[\"中文标题\"] (no Chinese in IDs)\n"
        "- No inline comments (remove %% lines)\n"
        "- Do not use special shapes like [(...)], just use [\"...\"]\n"
        "- One statement per line; do not glue multiple edges on one line\n"
        "- Do not use multi-source edges with &: expand into multiple edges\n"
    )
    user = json.dumps({"items": payload}, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def iter_batches(items: list[MermaidIssue], batch_size: int) -> Iterable[list[MermaidIssue]]:
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]


def _parse_repairs(response: str) -> dict[str, str]:
    parsed = parse_json(response)
    items = parsed.get("items", [])
    repairs: dict[str, str] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            issue_id = item.get("id")
            mermaid = item.get("mermaid")
            if isinstance(issue_id, str) and isinstance(mermaid, str):
                repairs[issue_id] = mermaid
    return repairs


def strip_mermaid_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.replace("mermaid", "", 1)
    return stripped.strip()


async def repair_batch(
    issues: list[MermaidIssue],
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


async def fix_mermaid_text(
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
    stats: MermaidFixStats,
    repair_enabled: bool = True,
    spans: list[MermaidSpan] | None = None,
    allowed_keys: set[tuple[int, str | None, int | None]] | None = None,
    progress_cb: Callable[[], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    replacements: list[tuple[int, int, str]] = []
    issues: list[MermaidIssue] = []
    if spans is None:
        spans = extract_mermaid_spans(text, context_chars)
    if allowed_keys:
        filtered: list[MermaidSpan] = []
        for span in spans:
            line_no = line_offset + span.line - 1
            if (line_no, field_path, item_index) in allowed_keys:
                filtered.append(span)
        spans = filtered
    if not spans:
        return text, []
    stats.diagrams_total += len(spans)
    file_id = short_hash(file_path)
    for idx, span in enumerate(spans):
        errors: list[str] = []
        original = span.content
        validation = validate_mermaid(original)
        candidate = original
        if validation:
            candidate = cleanup_mermaid(original)
            if candidate != original:
                candidate_validation = validate_mermaid(candidate)
                if not candidate_validation:
                    replacements.append((span.start, span.end, candidate))
                    if progress_cb:
                        progress_cb()
                    continue
                validation = candidate_validation
            errors.append(validation)
            stats.diagrams_invalid += 1
            issue_id = f"{file_id}:{idx}"
            issues.append(
                MermaidIssue(
                    issue_id=issue_id,
                    span=span,
                    errors=errors,
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
                    stats.diagrams_failed += 1
                    error_records.append({
                        "path": file_path,
                        "line": line_offset + issue.span.line - 1,
                        "mermaid": issue.span.content,
                        "errors": issue.errors + [f"batch_exception: {error}"],
                        "field_path": issue.field_path,
                        "item_index": issue.item_index,
                    })
                continue
            
            repairs, error = result
            
            if error:
                for issue in batch:
                    stats.diagrams_failed += 1
                    error_records.append(
                        {
                            "path": file_path,
                            "line": line_offset + issue.span.line - 1,
                            "mermaid": issue.span.content,
                            "errors": issue.errors + [f"llm_error: {error}"],
                            "field_path": issue.field_path,
                            "item_index": issue.item_index,
                        }
                    )
                continue

            for issue in batch:
                repaired = repairs.get(issue.issue_id)
                if not repaired:
                    stats.diagrams_failed += 1
                    error_records.append(
                        {
                            "path": file_path,
                            "line": line_offset + issue.span.line - 1,
                            "mermaid": issue.span.content,
                            "errors": issue.errors + ["llm_missing_output"],
                            "field_path": issue.field_path,
                            "item_index": issue.item_index,
                        }
                    )
                    continue
                repaired = strip_mermaid_fences(repaired)
                repaired = cleanup_mermaid(repaired)
                validation = validate_mermaid(repaired)
                if validation:
                    stats.diagrams_failed += 1
                    error_records.append(
                        {
                            "path": file_path,
                            "line": line_offset + issue.span.line - 1,
                            "mermaid": issue.span.content,
                            "errors": issue.errors + [validation],
                            "field_path": issue.field_path,
                            "item_index": issue.item_index,
                        }
                    )
                    continue
                stats.diagrams_repaired += 1
                replacements.append((issue.span.start, issue.span.end, repaired))
    elif issues:
        for issue in issues:
            stats.diagrams_failed += 1
            error_records.append(
                {
                    "path": file_path,
                    "line": line_offset + issue.span.line - 1,
                    "mermaid": issue.span.content,
                    "errors": issue.errors + ["validation_only"],
                    "field_path": issue.field_path,
                    "item_index": issue.item_index,
                }
            )

    updated = apply_replacements(text, replacements)
    return updated, error_records


def extract_diagrams_from_text(
    text: str,
    file_path: Path,
    line_offset: int,
    field_path: str | None,
    item_index: int | None,
    context_chars: int,
    skip_validation: bool = False,
) -> list[DiagramTask]:
    """Extract all diagram tasks from a text block.
    
    Args:
        skip_validation: If True, skip validation and mark all diagrams as having issues.
                        This is faster for initial extraction when you'll validate later.
    """
    tasks: list[DiagramTask] = []
    spans = extract_mermaid_spans(text, context_chars)
    file_id = short_hash(str(file_path))
    
    for idx, span in enumerate(spans):
        issue: MermaidIssue | None = None
        
        if skip_validation:
            # Mark all diagrams as needing validation (skip expensive mmdc call)
            issue_id = f"{file_id}:{line_offset + span.line - 1}:{idx}"
            issue = MermaidIssue(
                issue_id=issue_id,
                span=span,
                errors=["not_validated"],
                field_path=field_path,
                item_index=item_index,
            )
        else:
            # Full validation (expensive)
            validation = validate_mermaid(span.content)
            
            if validation:
                # Try cleanup first
                candidate = cleanup_mermaid(span.content)
                if candidate != span.content:
                    candidate_validation = validate_mermaid(candidate)
                    if not candidate_validation:
                        # Cleanup fixed it, no issue
                        pass
                    else:
                        validation = candidate_validation
                
                if validation:
                    # Still invalid after cleanup
                    issue_id = f"{file_id}:{line_offset + span.line - 1}:{idx}"
                    issue = MermaidIssue(
                        issue_id=issue_id,
                        span=span,
                        errors=[validation],
                        field_path=field_path,
                        item_index=item_index,
                    )
        
        tasks.append(
            DiagramTask(
                file_path=file_path,
                file_line_offset=line_offset,
                field_path=field_path,
                item_index=item_index,
                span=span,
                issue=issue,
            )
        )
    
    return tasks


async def repair_all_diagrams_global(
    tasks: list[DiagramTask],
    batch_size: int,
    max_concurrent_batches: int,
    provider,
    model_name: str,
    api_key: str | None,
    timeout: float,
    max_retries: int,
    client: httpx.AsyncClient,
    stats: MermaidFixStats,
    progress_cb: Callable[[], None] | None = None,
) -> tuple[dict[Path, list[tuple[int, int, str]]], list[dict[str, Any]]]:
    """
    Globally repair all diagrams in parallel.
    
    Returns:
        - dict mapping file paths to list of (start, end, replacement) tuples
        - list of error records
    """
    from collections import defaultdict

    stats.diagrams_total += len(tasks)

    file_replacements: dict[Path, list[tuple[int, int, str]]] = defaultdict(list)
    error_records: list[dict[str, Any]] = []

    clean_tasks: list[DiagramTask] = []
    invalid_tasks: list[DiagramTask] = []
    needs_validation: list[DiagramTask] = []
    task_by_issue_id: dict[str, DiagramTask] = {}

    for task in tasks:
        if not task.issue:
            clean_tasks.append(task)
            continue
        if task.issue.errors == ["not_validated"]:
            needs_validation.append(task)
            continue
        invalid_tasks.append(task)
        task_by_issue_id[task.issue.issue_id] = task

    if progress_cb:
        for _ in clean_tasks:
            progress_cb()

    if needs_validation:
        validate_limit = max(1, min(8, max_concurrent_batches))
        validate_semaphore = asyncio.Semaphore(validate_limit)

        def validate_and_cleanup(text: str) -> tuple[str, str | None]:
            validation = validate_mermaid(text)
            if not validation:
                return "clean", None
            cleaned = cleanup_mermaid(text)
            if cleaned != text and not validate_mermaid(cleaned):
                return "cleaned", cleaned
            return "invalid", validation

        async def validate_one(task: DiagramTask) -> tuple[str, str | None]:
            async with validate_semaphore:
                return await asyncio.to_thread(validate_and_cleanup, task.span.content)

        results = await asyncio.gather(*[validate_one(task) for task in needs_validation])
        for task, (status, payload) in zip(needs_validation, results):
            if status == "clean":
                if progress_cb:
                    progress_cb()
                continue
            if status == "cleaned":
                stats.diagrams_repaired += 1
                file_replacements[task.file_path].append((task.span.start, task.span.end, payload or task.span.content))
                if progress_cb:
                    progress_cb()
                continue

            # Still invalid: attach validation errors and send to LLM repair.
            task.issue.errors = [payload] if payload else ["invalid"]
            invalid_tasks.append(task)
            task_by_issue_id[task.issue.issue_id] = task

    stats.diagrams_invalid += len(invalid_tasks)

    if not invalid_tasks:
        return file_replacements, error_records

    issues = [task.issue for task in invalid_tasks if task.issue]
    batches = list(iter_batches(issues, batch_size))

    semaphore = asyncio.Semaphore(max_concurrent_batches)

    async def process_batch(batch: list[MermaidIssue]) -> tuple[dict[str, str], str | None]:
        async with semaphore:
            return await repair_batch(batch, provider, model_name, api_key, timeout, max_retries, client)

    results = await asyncio.gather(
        *[process_batch(batch) for batch in batches],
        return_exceptions=True,
    )

    for batch, result in zip(batches, results):
        if isinstance(result, Exception):
            error_msg = str(result)
            for issue in batch:
                stats.diagrams_failed += 1
                task = task_by_issue_id.get(issue.issue_id)
                if not task:
                    continue
                error_records.append(
                    {
                        "path": str(task.file_path),
                        "line": task.file_line_offset + issue.span.line - 1,
                        "mermaid": issue.span.content,
                        "errors": issue.errors + [f"batch_error: {error_msg}"],
                        "field_path": issue.field_path,
                        "item_index": issue.item_index,
                    }
                )
                if progress_cb:
                    progress_cb()
            continue

        repairs, batch_error = result

        if batch_error:
            for issue in batch:
                stats.diagrams_failed += 1
                task = task_by_issue_id.get(issue.issue_id)
                if not task:
                    continue
                error_records.append(
                    {
                        "path": str(task.file_path),
                        "line": task.file_line_offset + issue.span.line - 1,
                        "mermaid": issue.span.content,
                        "errors": issue.errors + [f"llm_error: {batch_error}"],
                        "field_path": issue.field_path,
                        "item_index": issue.item_index,
                    }
                )
                if progress_cb:
                    progress_cb()
            continue

        for issue in batch:
            task = task_by_issue_id.get(issue.issue_id)
            if not task:
                if progress_cb:
                    progress_cb()
                continue
            repaired = repairs.get(issue.issue_id)

            if not repaired:
                stats.diagrams_failed += 1
                error_records.append(
                    {
                        "path": str(task.file_path),
                        "line": task.file_line_offset + issue.span.line - 1,
                        "mermaid": issue.span.content,
                        "errors": issue.errors + ["llm_missing_output"],
                        "field_path": issue.field_path,
                        "item_index": issue.item_index,
                    }
                )
                if progress_cb:
                    progress_cb()
                continue

            repaired = strip_mermaid_fences(repaired)
            repaired = cleanup_mermaid(repaired)
            validation = validate_mermaid(repaired)

            if validation:
                stats.diagrams_failed += 1
                error_records.append(
                    {
                        "path": str(task.file_path),
                        "line": task.file_line_offset + issue.span.line - 1,
                        "mermaid": issue.span.content,
                        "errors": issue.errors + [f"repair_still_invalid: {validation}"],
                        "field_path": issue.field_path,
                        "item_index": issue.item_index,
                    }
                )
                if progress_cb:
                    progress_cb()
                continue

            stats.diagrams_repaired += 1
            file_replacements[task.file_path].append((issue.span.start, issue.span.end, repaired))
            if progress_cb:
                progress_cb()

    return file_replacements, error_records
