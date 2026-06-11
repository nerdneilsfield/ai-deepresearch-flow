"""Document chunking with template adapters."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable


@dataclass(frozen=True)
class SearchableField:
    field_name: str
    chunk_type: str
    text: str
    template_tag: str
    lang: str


@dataclass(frozen=True)
class Chunk:
    field_name: str
    chunk_type: str
    chunk_index: int
    text: str
    template_tag: str
    lang: str


_SHARED = ""
_NO_SPLIT_TYPES = {"title", "qa"}
_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_ENCODER: tuple[Callable[[str], list[Any]], Callable[[list[Any]], str]] | None = None


def _build_encoder() -> tuple[Callable[[str], list[Any]], Callable[[list[Any]], str]]:
    try:
        import tiktoken
    except ImportError:  # pragma: no cover - exercised when dependency is unavailable
        tiktoken = None

    if tiktoken is not None:
        encoding = tiktoken.get_encoding("cl100k_base")

        def encode(text: str) -> list[int]:
            return encoding.encode(text, disallowed_special=())

        def decode(tokens: list[int]) -> str:
            return encoding.decode(tokens)

        return encode, decode

    def encode(text: str) -> list[str]:
        return _TOKEN_RE.findall(text)

    def decode(tokens: list[str]) -> str:
        return " ".join(tokens)

    return encode, decode


def _get_encoder() -> tuple[Callable[[str], list[Any]], Callable[[list[Any]], str]]:
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = _build_encoder()
    return _ENCODER


def _resolve_title(record: dict[str, Any]) -> str | None:
    for key in ("paper_title", "title"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_text(record: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _count_tokens(text: str) -> int:
    encode, _ = _get_encoder()
    return len(encode(text))


def _sliding_window_split(text: str, *, max_tokens: int, overlap_tokens: int) -> list[str]:
    encode, decode = _get_encoder()
    tokens = encode(text)
    if len(tokens) <= max_tokens:
        return [text]

    step = max(max_tokens - overlap_tokens, 1)
    chunks: list[str] = []
    for start in range(0, len(tokens), step):
        segment = tokens[start : start + max_tokens]
        if not segment:
            break
        chunks.append(decode(segment))
        if start + max_tokens >= len(tokens):
            break
    return chunks


def _paragraph_first_split(text: str, *, max_tokens: int, overlap_tokens: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    accumulator: list[str] = []
    acc_tokens = 0

    for paragraph in paragraphs:
        paragraph_tokens = _count_tokens(paragraph)

        if paragraph_tokens > max_tokens:
            if accumulator:
                chunks.append("\n\n".join(accumulator))
                accumulator = []
                acc_tokens = 0
            chunks.extend(
                _sliding_window_split(
                    paragraph, max_tokens=max_tokens, overlap_tokens=overlap_tokens
                )
            )
            continue

        if accumulator and acc_tokens + paragraph_tokens > max_tokens:
            chunks.append("\n\n".join(accumulator))
            accumulator = []
            acc_tokens = 0

        accumulator.append(paragraph)
        acc_tokens += paragraph_tokens

    if accumulator:
        chunks.append("\n\n".join(accumulator))

    return chunks


def chunk_fields(
    fields: list[SearchableField],
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for field in fields:
        if not field.text or not field.text.strip():
            continue
        if field.chunk_type in _NO_SPLIT_TYPES:
            chunks.append(
                Chunk(
                    field_name=field.field_name,
                    chunk_type=field.chunk_type,
                    chunk_index=0,
                    text=field.text,
                    template_tag=field.template_tag,
                    lang=field.lang,
                )
            )
            continue

        for chunk_index, text in enumerate(
            _paragraph_first_split(field.text, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        ):
            chunks.append(
                Chunk(
                    field_name=field.field_name,
                    chunk_type=field.chunk_type,
                    chunk_index=chunk_index,
                    text=text,
                    template_tag=field.template_tag,
                    lang=field.lang,
                )
            )
    return chunks


def _extract_simple(record: dict[str, Any], tag: str) -> list[SearchableField]:
    fields: list[SearchableField] = []

    title = _resolve_title(record)
    if title:
        fields.append(SearchableField("title", "title", title, _SHARED, ""))

    abstract = _first_text(record, ("summary", "abstract"))
    if abstract:
        fields.append(SearchableField(f"{tag}/summary", "abstract", abstract, tag, ""))

    qa_items = record.get("qa") or record.get("qa_pairs") or []
    if isinstance(qa_items, list):
        for index, item in enumerate(qa_items):
            if not isinstance(item, dict):
                continue
            question = item.get("question", item.get("q", ""))
            answer = item.get("answer", item.get("a", ""))
            if isinstance(question, str):
                question = question.strip()
            else:
                question = ""
            if isinstance(answer, str):
                answer = answer.strip()
            else:
                answer = ""
            if question or answer:
                fields.append(
                    SearchableField(
                        f"{tag}/qa[{index}]",
                        "qa",
                        f"Q: {question}\nA: {answer}".strip(),
                        tag,
                        "",
                    )
                )

    excluded = {
        "paper_title",
        "title",
        "summary",
        "abstract",
        "qa",
        "qa_pairs",
    }
    for key, value in record.items():
        if key in excluded or not isinstance(value, str) or not value.strip():
            continue
        fields.append(SearchableField(f"{tag}/{key}", "content", value.strip(), tag, ""))

    return fields


def _extract_fallback(record: dict[str, Any], tag: str) -> list[SearchableField]:
    fields: list[SearchableField] = []

    title = _resolve_title(record)
    if title:
        fields.append(SearchableField("title", "title", title, _SHARED, ""))

    for key, value in record.items():
        if key in {"paper_title", "title"}:
            continue
        if isinstance(value, str) and value.strip():
            fields.append(SearchableField(f"{tag}/{key}", "content", value.strip(), tag, ""))

    return fields


_ADAPTERS: dict[str, Callable[[dict[str, Any], str], list[SearchableField]]] = {
    "simple": _extract_simple,
    "simple_phi": _extract_simple,
}


def extract_searchable_fields(record: dict[str, Any], template_tag: str) -> list[SearchableField]:
    adapter = _ADAPTERS.get(template_tag, _extract_fallback)
    return adapter(record, template_tag)
