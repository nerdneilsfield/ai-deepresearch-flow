"""Translation engine for OCR markdown."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import difflib
import logging
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Optional, Protocol

import httpx

from deepresearch_flow.paper.config import ProviderConfig, resolve_api_keys
from deepresearch_flow.paper.llm import call_provider, backoff_delay
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.translator.config import TranslateConfig
from deepresearch_flow.translator.fixers import fix_markdown
from deepresearch_flow.translator.placeholder import PlaceHolderStore
from deepresearch_flow.translator.prompts import build_translation_messages
from deepresearch_flow.translator.protector import MarkdownProtector
from deepresearch_flow.translator.segment import Node, reassemble_segments, split_to_segments


logger = logging.getLogger(__name__)


class TranslationProgress(Protocol):
    async def add_groups(self, count: int) -> None:
        ...

    async def advance_groups(self, count: int) -> None:
        ...

    async def set_group_status(self, text: str) -> None:
        ...


@dataclass
class TranslationResult:
    translated_text: str
    protected_text: str
    placeholder_store: PlaceHolderStore
    nodes: dict[int, Node]
    stats: "TranslationStats"


@dataclass
class DumpSnapshot:
    stage: str
    nodes: dict[int, Node] | None = None
    protected_text: str | None = None
    placeholder_store: PlaceHolderStore | None = None
    request_log: list[dict[str, Any]] | None = None


@dataclass
class TranslationStats:
    total_nodes: int
    success_nodes: int
    failed_nodes: int
    skipped_nodes: int
    initial_groups: int
    retry_groups: int
    retry_rounds: int


class KeyRotator:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self._idx = 0
        self._lock = asyncio.Lock()

    async def next_key(self) -> str | None:
        if not self._keys:
            return None
        async with self._lock:
            key = self._keys[self._idx % len(self._keys)]
            self._idx += 1
            return key


class RequestThrottle:
    def __init__(self, sleep_every: int, sleep_time: float) -> None:
        if sleep_every <= 0 or sleep_time <= 0:
            raise ValueError("sleep_every and sleep_time must be positive")
        self.sleep_every = sleep_every
        self.sleep_time = sleep_time
        self._count = 0
        self._lock = asyncio.Lock()

    async def tick(self) -> None:
        async with self._lock:
            self._count += 1
            if self._count % self.sleep_every == 0:
                await asyncio.sleep(self.sleep_time)


class MarkdownTranslator:
    def __init__(self, cfg: TranslateConfig) -> None:
        self.cfg = cfg
        self.protector = MarkdownProtector()
        self._rumdl_path = shutil.which("rumdl")
        self._rumdl_warned = False

        self._rx_preserve = re.compile(
            r"@@PRESERVE_(\d+)@@[\s\S]*?@@/PRESERVE_\1@@", re.DOTALL
        )
        self._rx_placeholder = re.compile(r"__PH_[A-Z0-9_]+__")
        self._rx_placeholder_fuzzy = re.compile(
            r"__PH[^A-Za-z0-9]*([A-Za-z0-9]+)[^0-9]*([0-9]{6})__"
        )
        self._rx_latex_dbl = re.compile(r"\$\$[\s\S]*?\$\$", re.DOTALL)
        self._rx_latex_sgl = re.compile(r"\$[^$]*?\$")
        self._rx_latex_pi = re.compile(r"\\\((?:.|\n)*?\\\)", re.DOTALL)
        self._rx_latex_br = re.compile(r"\\\[(?:.|\n)*?\\\]", re.DOTALL)
        self._rx_html_tag = re.compile(r"</?[^>]+>")
        self._rx_code_fence = re.compile(r"```[\s\S]*?```", re.DOTALL)
        self._rx_code_inline = re.compile(r"`[^`]*`")
        self._rx_url = re.compile(r"https?://\S+|www\.\S+")
        self._rx_letters = re.compile(
            r"[A-Za-z\u00C0-\u024F\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]"
        )

        self._rx_node_unpack = re.compile(
            r"(?:<|@@)NODE_START_(\d{4})(?:>|@@)(.*?)(?:</NODE_END_\1>|@@NODE_END_\1@@)",
            re.DOTALL,
        )

    def _strip_untranslatables(self, s: str) -> str:
        s = self._rx_preserve.sub("", s)
        s = self._rx_placeholder.sub("", s)
        s = self._rx_latex_dbl.sub("", s)
        s = self._rx_latex_sgl.sub("", s)
        s = self._rx_latex_pi.sub("", s)
        s = self._rx_latex_br.sub("", s)
        s = self._rx_code_fence.sub("", s)
        s = self._rx_code_inline.sub("", s)
        s = self._rx_html_tag.sub("", s)
        s = self._rx_url.sub("", s)
        s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
        return s

    def _is_placeholder_only(self, s: str) -> bool:
        core = self._strip_untranslatables(s)
        return not bool(self._rx_letters.search(core))

    def _placeholders_multiset(self, s: str) -> list[str]:
        return sorted(self._rx_placeholder.findall(s))

    def _normalize_for_compare(self, s: str) -> str:
        s = self._rx_placeholder.sub("", s)
        s = self._rx_latex_dbl.sub("", s)
        s = self._rx_latex_sgl.sub("", s)
        s = self._rx_latex_pi.sub("", s)
        s = self._rx_latex_br.sub("", s)
        s = self._rx_code_fence.sub("", s)
        s = self._rx_code_inline.sub("", s)
        s = self._rx_html_tag.sub("", s)
        s = self._rx_url.sub("", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _contains_target_script(self, s: str, target_lang: str) -> bool:
        tl = (target_lang or "").lower()
        if tl.startswith("zh"):
            return bool(re.search(r"[\u4E00-\u9FFF]", s))
        if tl.startswith(("ja", "jp")):
            return bool(re.search(r"[\u3040-\u30FF\u4E00-\u9FFF]", s))
        if tl.startswith("en"):
            return bool(re.search(r"[A-Za-z]", s))
        return True

    def _looks_like_identifier(self, s: str) -> bool:
        text = s.strip()
        if not text:
            return False
        if "__PH_" in text:
            core = self._strip_untranslatables(text)
            if len(core) <= 2:
                return True
        if re.search(r"\b(?:isbn|issn|doi|arxiv)\b", text, flags=re.IGNORECASE):
            return True
        if re.search(r"\b(?:acm|ieee)\b", text, flags=re.IGNORECASE):
            return True
        if re.search(
            r"\b\S+\.(?:pdf|png|jpg|jpeg|gif|svg|tex|bib|csv|json|md)\b",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        letters = re.findall(r"[A-Za-z]", text)
        if not letters:
            return True
        if text.upper() == text and len(re.findall(r"[A-Z]+", text)) <= 6:
            return True
        if len(re.findall(r"[A-Za-z]+", text)) <= 2 and len(text) <= 24:
            return True
        return False

    def _looks_like_person_name(self, s: str) -> bool:
        text = s.strip()
        if not text or len(text) > 80:
            return False
        particles = {
            "van",
            "von",
            "de",
            "del",
            "der",
            "da",
            "di",
            "la",
            "le",
            "du",
            "al",
            "bin",
            "ibn",
            "dos",
            "das",
            "mac",
            "mc",
        }
        suffixes = {"jr", "sr", "ii", "iii", "iv"}
        cleaned_parts: list[str] = []
        for raw in re.split(r"\s+", text):
            part = raw.strip().strip(",.;:*†‡")
            part = part.strip("()[]{}")
            part = re.sub(r"\d+$", "", part)
            part = part.strip(",.;:*†‡")
            if part:
                cleaned_parts.append(part)
        if len(cleaned_parts) < 2 or len(cleaned_parts) > 6:
            return False
        valid = 0
        for part in cleaned_parts:
            lower = part.lower()
            if lower in particles or lower in suffixes:
                continue
            if re.match(r"^[A-Z]\.?$", part):
                valid += 1
                continue
            if re.match(r"^[A-Z][A-Za-z]+(?:[-'][A-Za-z]+)*\.?$", part):
                valid += 1
                continue
            return False
        return valid >= 2

    def _is_translation_success(self, orig: str, trans: str) -> bool:
        if self._placeholders_multiset(orig) != self._placeholders_multiset(trans):
            return False
        if self._is_placeholder_only(orig):
            return bool(trans and trans.strip())
        if not trans or not trans.strip():
            return False
        core = self._strip_untranslatables(orig)
        if not bool(self._rx_letters.search(core)):
            return True
        ratio = difflib.SequenceMatcher(
            None, self._normalize_for_compare(orig), self._normalize_for_compare(trans)
        ).ratio()
        if ratio < 0.92:
            return True
        if self._contains_target_script(trans, self.cfg.target_lang):
            return True
        return self._looks_like_identifier(orig) or self._looks_like_person_name(orig)

    def _translation_failure_reason(self, orig: str, trans: str) -> str | None:
        if self._placeholders_multiset(orig) != self._placeholders_multiset(trans):
            return "placeholders_mismatch"
        if self._is_placeholder_only(orig):
            if not trans or not trans.strip():
                return "placeholder_only_empty"
            return None
        if not trans or not trans.strip():
            return "empty_output"
        core = self._strip_untranslatables(orig)
        if not bool(self._rx_letters.search(core)):
            return None
        ratio = difflib.SequenceMatcher(
            None, self._normalize_for_compare(orig), self._normalize_for_compare(trans)
        ).ratio()
        if ratio >= 0.92 and not self._contains_target_script(trans, self.cfg.target_lang):
            if self._looks_like_identifier(orig):
                return None
            if self._looks_like_person_name(orig):
                return None
            return f"missing_target_script ratio={ratio:.2f}"
        return None

    def _fix_placeholder_typos(self, text: str, valid_placeholders: set[str]) -> str:
        def replace(match: re.Match[str]) -> str:
            kind = match.group(1).upper()
            num = match.group(2)
            candidate = f"__PH_{kind}_{num}__"
            if candidate in valid_placeholders:
                return candidate
            return match.group(0)

        return self._rx_placeholder_fuzzy.sub(replace, text)

    def _align_placeholders(self, orig: str, trans: str) -> str:
        orig_phs = self._rx_placeholder.findall(orig)
        trans_phs = self._rx_placeholder.findall(trans)
        if not orig_phs and not trans_phs:
            return trans
        if not orig_phs:
            return self._rx_placeholder.sub("", trans)
        if not trans_phs:
            joiner = " " if trans and not trans.endswith((" ", "\n")) else ""
            return f"{trans}{joiner}{' '.join(orig_phs)}"
        parts = self._rx_placeholder.split(trans)
        out = parts[0]
        used = 0
        for idx in range(len(trans_phs)):
            if used < len(orig_phs):
                out += orig_phs[used]
                used += 1
            out += parts[idx + 1]
        if used < len(orig_phs):
            joiner = " " if out and not out.endswith((" ", "\n")) else ""
            out += f"{joiner}{' '.join(orig_phs[used:])}"
        return out

    def _summarize_text(self, text: str, limit: int = 160) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) > limit:
            return f"{compact[:limit]}…"
        return compact

    def _log_failed_sample(self, failed_nodes: dict[int, Node], label: str) -> None:
        if not logger.isEnabledFor(logging.DEBUG) or not failed_nodes:
            return
        sample_ids = list(sorted(failed_nodes.keys()))[:5]
        for nid in sample_ids:
            node = failed_nodes[nid]
            reason = self._translation_failure_reason(node.origin_text, node.translated_text)
            logger.debug(
                "Failed node %d (%s) reason=%s origin=%s translated=%s",
                nid,
                label,
                reason or "unknown",
                self._summarize_text(node.origin_text),
                self._summarize_text(node.translated_text),
            )

    def _normalize_markdown_blocks(self, text: str) -> str:
        text = self._normalize_markdown_images(text)
        return self._normalize_markdown_block_math(text)

    async def _format_markdown(self, text: str, stage: str) -> str:
        if not text.strip():
            return text
        if not self._rumdl_path:
            if not self._rumdl_warned:
                logger.warning(
                    "rumdl not available; skip markdown formatting (stage=%s)",
                    stage,
                )
                self._rumdl_warned = True
            return text

        def run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self._rumdl_path, "fmt", "--stdin", "--quiet"],
                input=text,
                text=True,
                capture_output=True,
            )

        result = await asyncio.to_thread(run)
        if result.returncode != 0:
            logger.warning(
                "rumdl fmt failed (stage=%s, rc=%s): %s",
                stage,
                result.returncode,
                (result.stderr or "").strip() or "unknown error",
            )
            return text
        return result.stdout or text

    def _normalize_markdown_images(self, text: str) -> str:
        lines = text.splitlines()
        out: list[str] = []
        in_fence = False
        fence_char = ""
        fence_len = 0
        img_re = re.compile(r"!\[[^\]]*\]\((?:[^)\\]|\\.)*\)")
        list_re = re.compile(r"^\s{0,3}(-|\*|\+|\d{1,9}\.)\s+")

        for line in lines:
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
                out.append(line)
                continue
            if in_fence:
                out.append(line)
                continue
            match = img_re.search(line)
            if not match:
                out.append(line)
                continue
            if list_re.match(line) or (line.lstrip().startswith("|") and line.count("|") >= 2):
                out.append(line)
                continue
            prefix = line[:match.start()]
            suffix = line[match.end():]
            prefix_text = prefix.strip()
            suffix_text = suffix.strip()
            indent = prefix if not prefix_text else ""
            if prefix_text:
                out.append(prefix.rstrip())
                out.append("")
            elif out and out[-1].strip():
                out.append("")
            out.append(f"{indent}{line[match.start():match.end()]}")
            if suffix_text:
                out.append("")
                out.append(suffix.strip())
            elif out and out[-1].strip():
                out.append("")
        return "\n".join(out)

    def _normalize_markdown_block_math(self, text: str) -> str:
        lines = text.splitlines()
        out: list[str] = []
        in_fence = False
        fence_char = ""
        fence_len = 0
        in_math = False

        for idx, line in enumerate(lines):
            stripped = line.strip()
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
                out.append(line)
                continue
            if in_fence:
                out.append(line)
                continue
            if not in_math and stripped in {"$$", "\\["}:
                if out and out[-1].strip():
                    out.append("")
                out.append(line)
                in_math = True
                continue
            if in_math:
                out.append(line)
                if stripped in {"$$", "\\]"}:
                    in_math = False
                    next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
                    if next_line.strip():
                        out.append("")
                continue
            out.append(line)
        return "\n".join(out)

    def _group_nodes(
        self,
        nodes: dict[int, Node],
        only_ids: Optional[list[int]] = None,
        max_chunk_chars: Optional[int] = None,
        include_translated: bool = False,
    ) -> list[str]:
        groups: list[str] = []
        cur_group = ""
        limit = max_chunk_chars or self.cfg.max_chunk_chars

        ids = sorted(only_ids if only_ids is not None else nodes.keys())
        for nid in ids:
            node = nodes[nid]
            if (not include_translated) and node.translated_text:
                continue
            id_str = f"{nid:04d}"
            node_str = f"<NODE_START_{id_str}>\n{node.origin_text}\n</NODE_END_{id_str}>\n"
            if len(cur_group) + len(node_str) > limit and cur_group:
                groups.append(cur_group)
                cur_group = ""
            cur_group += node_str
        if cur_group:
            groups.append(cur_group)
        return groups

    def _ungroup_nodes(self, group_text: str, origin_nodes: dict[int, Node]) -> dict[int, Node]:
        nodes: dict[int, Node] = {}
        for match in self._rx_node_unpack.finditer(group_text):
            node_id = int(match.group(1))
            if node_id not in origin_nodes:
                continue
            nodes[node_id] = Node(
                nid=node_id,
                origin_text=origin_nodes[node_id].origin_text,
                translated_text=match.group(2),
            )
        return nodes

    def _ungroup_groups(
        self,
        groups: list[str],
        origin_nodes: dict[int, Node],
        fill_missing: bool = True,
    ) -> dict[int, Node]:
        nodes: dict[int, Node] = {}
        for group_text in groups:
            nodes.update(self._ungroup_nodes(group_text, origin_nodes))
        if fill_missing:
            for nid, node in origin_nodes.items():
                if nid not in nodes:
                    nodes[nid] = node
        return nodes

    def _collect_failed_nodes(self, nodes: dict[int, Node]) -> dict[int, Node]:
        failed: dict[int, Node] = {}
        for nid, node in nodes.items():
            ok = self._is_translation_success(node.origin_text, node.translated_text) if node.translated_text else False
            if not ok:
                failed[nid] = node
        return failed

    async def _translate_group(
        self,
        group_text: str,
        provider: ProviderConfig,
        model: str,
        client: httpx.AsyncClient,
        api_key: str | None,
        timeout: float,
        semaphore: asyncio.Semaphore,
        throttle: RequestThrottle | None,
        max_tokens: int | None,
        max_retries: int,
        request_log: list[dict[str, Any]] | None,
        stage: str,
        group_index: int,
        dump_callback: Callable[[DumpSnapshot], None] | None,
    ) -> str:
        attempts = 0
        while True:
            attempts += 1
            if throttle:
                await throttle.tick()
            messages = build_translation_messages(
                self.cfg.source_lang, self.cfg.target_lang, group_text
            )
            start_time = time.time()
            try:
                async with semaphore:
                    response = await call_provider(
                        provider,
                        model,
                        messages,
                        {},
                        api_key,
                        timeout,
                        "none",
                        client,
                        max_tokens=max_tokens,
                    )
                elapsed_ms = int((time.time() - start_time) * 1000)
                if request_log is not None:
                    request_log.append(
                        {
                            "stage": stage,
                            "group_index": group_index,
                            "attempt": attempts,
                            "provider": provider.name,
                            "model": model,
                            "messages": messages,
                            "response": response,
                            "elapsed_ms": elapsed_ms,
                        }
                    )
                    if dump_callback is not None:
                        dump_callback(DumpSnapshot(stage=stage, request_log=request_log))
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "Group translated: stage=%s group=%d attempt=%d chars=%d elapsed_ms=%d",
                        stage,
                        group_index,
                        attempts,
                        len(group_text),
                        elapsed_ms,
                    )
                return response
            except ProviderError as exc:
                elapsed_ms = int((time.time() - start_time) * 1000)
                if request_log is not None:
                    request_log.append(
                        {
                            "stage": stage,
                            "group_index": group_index,
                            "attempt": attempts,
                            "provider": provider.name,
                            "model": model,
                            "messages": messages,
                            "error": str(exc),
                            "retryable": exc.retryable,
                            "elapsed_ms": elapsed_ms,
                        }
                    )
                    if dump_callback is not None:
                        dump_callback(DumpSnapshot(stage=stage, request_log=request_log))
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "Group failed: stage=%s group=%d attempt=%d retryable=%s elapsed_ms=%d error=%s",
                        stage,
                        group_index,
                        attempts,
                        exc.retryable,
                        elapsed_ms,
                        exc,
                    )
                if exc.retryable and attempts < max_retries:
                    await asyncio.sleep(backoff_delay(1.0, attempts, 20.0))
                    continue
                raise

    async def translate(
        self,
        text: str,
        provider: ProviderConfig,
        model: str,
        client: httpx.AsyncClient,
        api_keys: list[str],
        timeout: float,
        semaphore: asyncio.Semaphore,
        throttle: RequestThrottle | None,
        max_tokens: int | None,
        fix_level: str,
        progress: TranslationProgress | None = None,
        fallback_provider: ProviderConfig | None = None,
        fallback_model: str | None = None,
        fallback_max_tokens: int | None = None,
        fallback_provider_2: ProviderConfig | None = None,
        fallback_model_2: str | None = None,
        fallback_max_tokens_2: int | None = None,
        fallback_retry_times: int | None = None,
        fallback_retry_times_2: int | None = None,
        format_enabled: bool = True,
        request_log: list[dict[str, Any]] | None = None,
        dump_callback: Callable[[DumpSnapshot], None] | None = None,
        group_concurrency: int = 1,
    ) -> TranslationResult:
        if fix_level != "off":
            text = fix_markdown(text, fix_level)
        if format_enabled:
            text = await self._format_markdown(text, "pre")

        store = PlaceHolderStore()
        protected = self.protector.protect(text, self.cfg, store)
        if dump_callback is not None:
            dump_callback(
                DumpSnapshot(
                    stage="protected",
                    protected_text=protected,
                    placeholder_store=store,
                    request_log=request_log,
                )
            )
        segments, nodes = split_to_segments(protected, self.cfg.max_chunk_chars)
        total_nodes = len(nodes)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Segments: %d", len(segments))
            logger.debug("Nodes: %d", len(nodes))

        skip_count = 0
        for node in nodes.values():
            if self._is_placeholder_only(node.origin_text):
                node.translated_text = node.origin_text
                skip_count += 1
        if skip_count:
            logger.debug("Skipped %d placeholder-only nodes", skip_count)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Placeholder counts: %s", store.kind_counts())

        rotator = KeyRotator(resolve_api_keys(api_keys))
        max_retries = max(self.cfg.retry_times, 1)

        nodes_progress: dict[int, Node] | None = None
        if dump_callback is not None:
            nodes_progress = {
                nid: Node(
                    nid=nid,
                    origin_text=node.origin_text,
                    translated_text=node.translated_text,
                )
                for nid, node in nodes.items()
            }

        async def run_groups(
            groups: list[str],
            rotator: KeyRotator,
            stage: str,
            max_tokens_value: int | None,
            retry_limit_value: int,
            provider_value: ProviderConfig,
            model_value: str,
        ) -> list[str]:
            if not groups:
                return []
            outputs: list[str] = [""] * len(groups)

            async def run_one(idx: int, group_text: str) -> tuple[int, str]:
                api_key = await rotator.next_key()
                response = await self._translate_group(
                    group_text,
                    provider_value,
                    model_value,
                    client,
                    api_key,
                    timeout,
                    semaphore,
                    throttle,
                    max_tokens_value,
                    retry_limit_value,
                    request_log,
                    stage,
                    idx,
                    dump_callback,
                )
                return idx, response

            if group_concurrency <= 1:
                for idx, group_text in enumerate(groups):
                    idx_out, response = await run_one(idx, group_text)
                    outputs[idx_out] = response
                    if nodes_progress is not None:
                        nodes_progress.update(self._ungroup_nodes(response, nodes))
                        dump_callback(
                            DumpSnapshot(
                                stage=stage,
                                nodes=nodes_progress,
                                request_log=request_log,
                            )
                        )
                    if progress:
                        await progress.advance_groups(1)
                return outputs

            guard = asyncio.Semaphore(group_concurrency)

            async def guarded(idx: int, group_text: str) -> tuple[int, str]:
                async with guard:
                    return await run_one(idx, group_text)

            tasks = [asyncio.create_task(guarded(i, g)) for i, g in enumerate(groups)]
            try:
                for task in asyncio.as_completed(tasks):
                    idx_out, response = await task
                    outputs[idx_out] = response
                    if nodes_progress is not None:
                        nodes_progress.update(self._ungroup_nodes(response, nodes))
                        dump_callback(
                            DumpSnapshot(
                                stage=stage,
                                nodes=nodes_progress,
                                request_log=request_log,
                            )
                        )
                    if progress:
                        await progress.advance_groups(1)
            except Exception:
                for task in tasks:
                    task.cancel()
                raise

            return outputs

        groups = self._group_nodes(nodes)
        initial_groups = len(groups)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Groups: %d", len(groups))
        if progress:
            await progress.add_groups(len(groups))
        outputs = await run_groups(
            groups,
            rotator,
            "initial",
            max_tokens,
            max_retries,
            provider,
            model,
        )

        translated_nodes = self._ungroup_groups(outputs, nodes)
        valid_placeholders = set(store.snapshot().values())
        if valid_placeholders:
            for node in translated_nodes.values():
                if node.translated_text:
                    node.translated_text = self._fix_placeholder_typos(
                        node.translated_text, valid_placeholders
                    )
        for node in translated_nodes.values():
            if node.translated_text:
                node.translated_text = self._align_placeholders(
                    node.origin_text, node.translated_text
                )
        failed_nodes = self._collect_failed_nodes(translated_nodes)
        success_count = max(total_nodes - len(failed_nodes), 0)
        logger.info(
            "Initial translation: nodes=%d ok=%d fail=%d skip=%d groups=%d",
            total_nodes,
            success_count,
            len(failed_nodes),
            skip_count,
            initial_groups,
        )
        self._log_failed_sample(failed_nodes, "initial")
        if progress:
            await progress.set_group_status(
                f"nodes {total_nodes} ok {success_count} "
                f"fail {len(failed_nodes)} skip {skip_count}"
            )

        retry_groups_total = 0
        retry_rounds = 0
        retry_limit = max_retries
        retry_group_limit = self.cfg.retry_group_max_chars or max(
            1024, self.cfg.max_chunk_chars // 2
        )
        if self.cfg.retry_failed_nodes and failed_nodes:
            attempt = 1
            while failed_nodes and attempt <= retry_limit:
                retry_ids = sorted(failed_nodes.keys())
                retry_groups = self._group_nodes(
                    failed_nodes,
                    only_ids=retry_ids,
                    max_chunk_chars=retry_group_limit,
                    include_translated=True,
                )
                if not retry_groups:
                    break
                retry_rounds += 1
                retry_groups_total += len(retry_groups)
                logger.info(
                    "Retrying %d failed nodes in %d groups (round %d/%d)",
                    len(failed_nodes),
                    len(retry_groups),
                    attempt,
                    retry_limit,
                )
                if progress:
                    await progress.add_groups(len(retry_groups))
                retry_outputs = await run_groups(
                    retry_groups,
                    rotator,
                    f"retry-{attempt}",
                    max_tokens,
                    retry_limit,
                    provider,
                    model,
                )
                retry_nodes = self._ungroup_groups(
                    retry_outputs, failed_nodes, fill_missing=False
                )
                if valid_placeholders:
                    for node in retry_nodes.values():
                        if node.translated_text:
                            node.translated_text = self._fix_placeholder_typos(
                                node.translated_text, valid_placeholders
                            )
                for node in retry_nodes.values():
                    if node.translated_text:
                        node.translated_text = self._align_placeholders(
                            node.origin_text, node.translated_text
                        )
                for nid, node in retry_nodes.items():
                    translated_nodes[nid] = node
                failed_nodes = self._collect_failed_nodes(translated_nodes)
                success_count = max(total_nodes - len(failed_nodes), 0)
                logger.info(
                    "Retry round %d done: nodes=%d ok=%d fail=%d skip=%d",
                    attempt,
                    total_nodes,
                    success_count,
                    len(failed_nodes),
                    skip_count,
                )
                self._log_failed_sample(failed_nodes, f"retry-{attempt}")
                if progress:
                    await progress.set_group_status(
                        f"nodes {total_nodes} ok {success_count} "
                        f"fail {len(failed_nodes)} skip {skip_count}"
                    )
                attempt += 1

        if (
            self.cfg.retry_failed_nodes
            and failed_nodes
            and fallback_provider
            and fallback_model
        ):
            fallback_rotator = KeyRotator(resolve_api_keys(fallback_provider.api_keys))
            attempt = 1
            fallback_retry_limit = fallback_retry_times or retry_limit
            while failed_nodes and attempt <= fallback_retry_limit:
                retry_ids = sorted(failed_nodes.keys())
                retry_groups = self._group_nodes(
                    failed_nodes,
                    only_ids=retry_ids,
                    max_chunk_chars=retry_group_limit,
                    include_translated=True,
                )
                if not retry_groups:
                    break
                retry_rounds += 1
                retry_groups_total += len(retry_groups)
                logger.info(
                    "Fallback %s/%s: retrying %d failed nodes in %d groups (round %d/%d)",
                    fallback_provider.name,
                    fallback_model,
                    len(failed_nodes),
                    len(retry_groups),
                    attempt,
                    fallback_retry_limit,
                )
                if progress:
                    await progress.add_groups(len(retry_groups))
                retry_outputs = await run_groups(
                    retry_groups,
                    fallback_rotator,
                    f"fallback-{attempt}",
                    fallback_max_tokens,
                    fallback_retry_limit,
                    fallback_provider,
                    fallback_model,
                )
                retry_nodes = self._ungroup_groups(
                    retry_outputs, failed_nodes, fill_missing=False
                )
                if valid_placeholders:
                    for node in retry_nodes.values():
                        if node.translated_text:
                            node.translated_text = self._fix_placeholder_typos(
                                node.translated_text, valid_placeholders
                            )
                for node in retry_nodes.values():
                    if node.translated_text:
                        node.translated_text = self._align_placeholders(
                            node.origin_text, node.translated_text
                        )
                for nid, node in retry_nodes.items():
                    translated_nodes[nid] = node
                failed_nodes = self._collect_failed_nodes(translated_nodes)
                success_count = max(total_nodes - len(failed_nodes), 0)
                logger.info(
                    "Fallback round %d done: nodes=%d ok=%d fail=%d skip=%d",
                    attempt,
                    total_nodes,
                    success_count,
                    len(failed_nodes),
                    skip_count,
                )
                self._log_failed_sample(failed_nodes, f"fallback-{attempt}")
                if progress:
                    await progress.set_group_status(
                        f"nodes {total_nodes} ok {success_count} "
                        f"fail {len(failed_nodes)} skip {skip_count}"
                    )
                attempt += 1

        if (
            self.cfg.retry_failed_nodes
            and failed_nodes
            and fallback_provider_2
            and fallback_model_2
        ):
            fallback_rotator = KeyRotator(resolve_api_keys(fallback_provider_2.api_keys))
            attempt = 1
            fallback_retry_limit = fallback_retry_times_2 or retry_limit
            while failed_nodes and attempt <= fallback_retry_limit:
                retry_ids = sorted(failed_nodes.keys())
                retry_groups = self._group_nodes(
                    failed_nodes,
                    only_ids=retry_ids,
                    max_chunk_chars=retry_group_limit,
                    include_translated=True,
                )
                if not retry_groups:
                    break
                retry_rounds += 1
                retry_groups_total += len(retry_groups)
                logger.info(
                    "Fallback2 %s/%s: retrying %d failed nodes in %d groups (round %d/%d)",
                    fallback_provider_2.name,
                    fallback_model_2,
                    len(failed_nodes),
                    len(retry_groups),
                    attempt,
                    fallback_retry_limit,
                )
                if progress:
                    await progress.add_groups(len(retry_groups))
                retry_outputs = await run_groups(
                    retry_groups,
                    fallback_rotator,
                    f"fallback2-{attempt}",
                    fallback_max_tokens_2,
                    fallback_retry_limit,
                    fallback_provider_2,
                    fallback_model_2,
                )
                retry_nodes = self._ungroup_groups(
                    retry_outputs, failed_nodes, fill_missing=False
                )
                if valid_placeholders:
                    for node in retry_nodes.values():
                        if node.translated_text:
                            node.translated_text = self._fix_placeholder_typos(
                                node.translated_text, valid_placeholders
                            )
                for node in retry_nodes.values():
                    if node.translated_text:
                        node.translated_text = self._align_placeholders(
                            node.origin_text, node.translated_text
                        )
                for nid, node in retry_nodes.items():
                    translated_nodes[nid] = node
                failed_nodes = self._collect_failed_nodes(translated_nodes)
                success_count = max(total_nodes - len(failed_nodes), 0)
                logger.info(
                    "Fallback2 round %d done: nodes=%d ok=%d fail=%d skip=%d",
                    attempt,
                    total_nodes,
                    success_count,
                    len(failed_nodes),
                    skip_count,
                )
                self._log_failed_sample(failed_nodes, f"fallback2-{attempt}")
                if progress:
                    await progress.set_group_status(
                        f"nodes {total_nodes} ok {success_count} "
                        f"fail {len(failed_nodes)} skip {skip_count}"
                    )
                attempt += 1

        failed_count = len(failed_nodes)
        success_count = max(total_nodes - failed_count, 0)
        stats = TranslationStats(
            total_nodes=total_nodes,
            success_nodes=success_count,
            failed_nodes=failed_count,
            skipped_nodes=skip_count,
            initial_groups=initial_groups,
            retry_groups=retry_groups_total,
            retry_rounds=retry_rounds,
        )

        if logger.isEnabledFor(logging.DEBUG) and failed_nodes:
            sample_ids = list(sorted(failed_nodes.keys()))[:5]
            for nid in sample_ids:
                node = failed_nodes[nid]
                reason = self._translation_failure_reason(node.origin_text, node.translated_text)
                logger.debug(
                    "Failed node %d reason=%s origin=%s translated=%s",
                    nid,
                    reason or "unknown",
                    self._summarize_text(node.origin_text),
                    self._summarize_text(node.translated_text),
                )

        if failed_nodes:
            for nid in failed_nodes:
                translated_nodes[nid].translated_text = translated_nodes[nid].origin_text

        merged_text = reassemble_segments(segments, translated_nodes)
        restored = self.protector.unprotect(merged_text, store)
        if format_enabled:
            restored = await self._format_markdown(restored, "post")
        restored = self._normalize_markdown_blocks(restored)

        return TranslationResult(
            translated_text=restored,
            protected_text=protected,
            placeholder_store=store,
            nodes=translated_nodes,
            stats=stats,
        )
