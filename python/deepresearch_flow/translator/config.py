"""Translator configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TranslateConfig:
    source_lang: str | None = None
    target_lang: str = "zh"
    max_chunk_chars: int = 4000
    translate_tables: bool = False
    translate_links_text: bool = False
    translate_image_alt: bool = False
    strict_placeholder_check: bool = True
    retry_failed_nodes: bool = True
    retry_times: int = 3
    retry_group_max_chars: int | None = None
