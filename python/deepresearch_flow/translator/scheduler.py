"""Multi-document concurrent translation scheduler."""

from __future__ import annotations

import asyncio
import enum
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from deepresearch_flow.paper.config import ProviderConfig
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.paper.routing import RoutePool, RuntimeRoute
from deepresearch_flow.paper.utils import short_hash
from deepresearch_flow.translator.engine import KeyRotator, MarkdownTranslator, RequestThrottle
from deepresearch_flow.translator.placeholder import PlaceHolderStore
from deepresearch_flow.translator.progress import ProgressReporter
from deepresearch_flow.translator.segment import Node, Segment


logger = logging.getLogger(__name__)


class StateError(Exception):
    """Raised when a document state transition is invalid."""


class DocStage(enum.Enum):
    PREPROCESSED = "preprocessed"
    TRANSLATING = "translating"
    RETRYING = "retrying"
    FALLBACK_1 = "fallback_1"
    FALLBACK_2 = "fallback_2"
    FINALIZING = "finalizing"
    DONE = "done"


TRANSITIONS: dict[DocStage, list[DocStage]] = {
    DocStage.PREPROCESSED: [DocStage.TRANSLATING],
    DocStage.TRANSLATING: [DocStage.RETRYING, DocStage.FINALIZING],
    DocStage.RETRYING: [DocStage.FALLBACK_1, DocStage.FINALIZING],
    DocStage.FALLBACK_1: [DocStage.FALLBACK_2, DocStage.FINALIZING],
    DocStage.FALLBACK_2: [DocStage.FINALIZING],
    DocStage.FINALIZING: [DocStage.DONE],
}


STAGE_ORDER = [
    DocStage.TRANSLATING,
    DocStage.RETRYING,
    DocStage.FALLBACK_1,
    DocStage.FALLBACK_2,
]

_SENTINEL = object()


@dataclass(frozen=True)
class GroupTask:
    doc_id: str
    stage: DocStage
    group_index: int
    node_ids: tuple[int, ...]
    group_text: str


@dataclass(frozen=True)
class CompletionEvent:
    doc_id: str
    stage: DocStage
    group_index: int
    node_ids: tuple[int, ...]
    ok: bool
    response: str


@dataclass
class DocumentContext:
    doc_id: str
    source_path: Path
    output_path: Path
    original_text: str
    protected_text: str
    segments: list[Segment]
    nodes: dict[int, Node]
    store: PlaceHolderStore
    total_nodes: int = 0
    skip_count: int = 0
    initial_groups_count: int = 0
    initial_groups: list[str] = field(default_factory=list)
    translated_nodes: dict[int, Node] = field(default_factory=dict)
    pending_counts: dict[str, int] = field(default_factory=dict)
    stage: DocStage = DocStage.PREPROCESSED
    stage_group_counts: dict[str, int] = field(default_factory=dict)
    retry_rounds: int = 0
    request_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class QueueConfig:
    stage: DocStage
    workers: int
    provider_semaphore: asyncio.Semaphore
    route_pool: RoutePool | None
    provider: ProviderConfig
    model: str
    api_keys: list[Any]
    max_tokens: int | None
    retry_limit: int
    group_max_chars: int | None = None


GroupBuilder = Callable[[DocumentContext, dict[int, Node], DocStage], list[GroupTask]]
FinalizeFn = Callable[[DocumentContext], Awaitable[None]]
DoneCallback = Callable[[DocumentContext, Exception | None], Awaitable[None]]


class DocumentActor:
    """Single writer for one document's translation state."""

    def __init__(
        self,
        *,
        ctx: DocumentContext,
        queue_map: dict[DocStage, asyncio.Queue],
        available_stages: list[DocStage],
        max_inflight_per_doc: int,
        translator: MarkdownTranslator,
        group_builder: GroupBuilder,
        finalize_fn: FinalizeFn,
        on_done: DoneCallback,
        progress: ProgressReporter | None,
    ) -> None:
        self.ctx = ctx
        self._queue_map = queue_map
        self._available_stages = available_stages
        self._max_inflight_per_doc = max(1, max_inflight_per_doc)
        self._translator = translator
        self._group_builder = group_builder
        self._finalize_fn = finalize_fn
        self._on_done = on_done
        self._progress = progress
        self._pending_groups: list[GroupTask] = []
        self._done = False

    async def start(self, initial_groups: list[GroupTask]) -> None:
        self._transition_to(DocStage.TRANSLATING)
        self.ctx.pending_counts[DocStage.TRANSLATING.value] = len(initial_groups)
        self.ctx.stage_group_counts[DocStage.TRANSLATING.value] = len(initial_groups)
        self._pending_groups = list(initial_groups)
        if self._progress is not None and initial_groups:
            await self._progress.add_groups(DocStage.TRANSLATING.value, len(initial_groups))
        await self._emit_available()
        if not initial_groups:
            await self._advance_after_stage(DocStage.TRANSLATING)

    async def on_completion(self, event: CompletionEvent) -> None:
        if self._done:
            return
        if event.stage != self.ctx.stage:
            logger.debug(
                "Ignoring stale completion event for doc=%s stage=%s current_stage=%s",
                self.ctx.doc_id,
                event.stage.value,
                self.ctx.stage.value,
            )
            return
        valid_placeholders = self.ctx.store.placeholders()
        if event.ok:
            unpacked = self._translator._ungroup_nodes(event.response, self.ctx.nodes)
            for nid in event.node_ids:
                node = unpacked.get(nid)
                if node is None:
                    node = Node(
                        nid=nid,
                        origin_text=self.ctx.nodes[nid].origin_text,
                        translated_text="",
                    )
                elif node.translated_text:
                    if valid_placeholders:
                        node.translated_text = self._translator._fix_placeholder_typos(
                            node.translated_text, valid_placeholders
                        )
                    node.translated_text = self._translator._align_placeholders(
                        node.origin_text, node.translated_text
                    )
                self.ctx.translated_nodes[nid] = node
        else:
            for nid in event.node_ids:
                self.ctx.translated_nodes[nid] = Node(
                    nid=nid,
                    origin_text=self.ctx.nodes[nid].origin_text,
                    translated_text="",
                )

        stage_key = event.stage.value
        self.ctx.pending_counts[stage_key] = max(self.ctx.pending_counts.get(stage_key, 0) - 1, 0)
        if self._progress is not None:
            await self._progress.advance_groups(event.stage.value, 1)

        await self._emit_available()
        if self.ctx.pending_counts.get(stage_key, 0) == 0 and not self._pending_groups:
            await self._advance_after_stage(event.stage)

    async def _emit_available(self) -> None:
        queue = self._queue_map[self.ctx.stage]
        stage_key = self.ctx.stage.value
        while self._pending_groups and self._inflight_count(stage_key) < self._max_inflight_per_doc:
            await queue.put(self._pending_groups.pop(0))

    def _inflight_count(self, stage_key: str) -> int:
        return self.ctx.pending_counts.get(stage_key, 0) - len(self._pending_groups)

    async def _advance_after_stage(self, finished_stage: DocStage) -> None:
        failed = self._translator._collect_failed_nodes(self.ctx.translated_nodes)
        next_stage = self._next_stage_for(finished_stage, bool(failed))
        if next_stage is None:
            await self._finalize()
            return
        if next_stage is DocStage.FINALIZING:
            await self._finalize()
            return

        groups = self._group_builder(self.ctx, failed, next_stage)
        self._transition_to(next_stage)
        self.ctx.pending_counts[next_stage.value] = len(groups)
        self.ctx.stage_group_counts[next_stage.value] = len(groups)
        self._pending_groups = list(groups)
        if next_stage in {DocStage.RETRYING, DocStage.FALLBACK_1, DocStage.FALLBACK_2} and groups:
            self.ctx.retry_rounds += 1
        if self._progress is not None and groups:
            await self._progress.add_groups(next_stage.value, len(groups))
        await self._emit_available()
        if not groups:
            await self._advance_after_stage(next_stage)

    def _next_stage_for(self, current_stage: DocStage, has_failures: bool) -> DocStage | None:
        if not has_failures:
            return DocStage.FINALIZING
        try:
            idx = self._available_stages.index(current_stage)
        except ValueError as exc:
            raise StateError(f"Unknown stage {current_stage}") from exc
        if idx + 1 < len(self._available_stages):
            return self._available_stages[idx + 1]
        return DocStage.FINALIZING

    async def _finalize(self) -> None:
        self._transition_to(DocStage.FINALIZING)
        err: Exception | None = None
        try:
            await self._finalize_fn(self.ctx)
            self._transition_to(DocStage.DONE)
        except Exception as exc:  # pragma: no cover - surfaced through callback
            err = exc
        self._done = True
        await self._on_done(self.ctx, err)

    def _transition_to(self, next_stage: DocStage) -> None:
        if next_stage == self.ctx.stage:
            return
        allowed = TRANSITIONS.get(self.ctx.stage, [])
        if next_stage not in allowed:
            raise StateError(
                f"Invalid transition for doc {self.ctx.doc_id}: {self.ctx.stage.value} -> {next_stage.value}"
            )
        self.ctx.stage = next_stage


class Scheduler:
    """Runs multi-document translation with stage-specific worker pools."""

    def __init__(
        self,
        *,
        translator: MarkdownTranslator,
        document_window: int,
        global_semaphore: asyncio.Semaphore,
        queue_configs: list[QueueConfig],
        progress: ProgressReporter | None,
        client: httpx.AsyncClient,
        throttle: RequestThrottle | None,
        timeout: float,
    ) -> None:
        self._translator = translator
        self._document_window = max(1, document_window)
        self._global_sem = global_semaphore
        self._progress = progress
        self._client = client
        self._throttle = throttle
        self._timeout = timeout
        self._queues: dict[DocStage, asyncio.Queue] = {}
        self._configs: dict[DocStage, QueueConfig] = {}
        self._result_queue: asyncio.Queue[CompletionEvent] = asyncio.Queue()
        self._actors: dict[str, DocumentActor] = {}
        self._total_docs = 0
        self._done_count = 0
        self._all_done = asyncio.Event()
        self._failed_files: list[Path] = []
        for qc in queue_configs:
            self._queues[qc.stage] = asyncio.Queue()
            self._configs[qc.stage] = qc
        self._available_stages = [stage for stage in STAGE_ORDER if stage in self._queues]

    async def run(
        self,
        *,
        paths: list[Path],
        output_map: dict[Path, Path],
        fix_level: str,
        format_enabled: bool,
        request_log_enabled: bool = False,
        debug_root: Path | None = None,
        dump_protected: bool = False,
        dump_placeholders: bool = False,
        dump_nodes: bool = False,
        dump_requests_log: bool = False,
    ) -> list[Path]:
        self._total_docs = len(paths)
        if self._total_docs == 0:
            self._all_done.set()
            return []

        worker_tasks = [
            asyncio.create_task(self._worker(self._queues[qc.stage], qc))
            for qc in self._configs.values()
            for _ in range(qc.workers)
        ]
        dispatcher_task = asyncio.create_task(self._dispatcher())
        window_sem = asyncio.Semaphore(self._document_window)
        try:
            for path in paths:
                await window_sem.acquire()
                try:
                    ctx = await self._preprocess(
                        path=path,
                        output_path=output_map[path],
                        fix_level=fix_level,
                        format_enabled=format_enabled,
                        request_log_enabled=request_log_enabled,
                    )
                except Exception as exc:
                    logger.error("Failed to preprocess %s: %s", path, exc)
                    self._failed_files.append(path)
                    self._done_count += 1
                    if self._progress is not None:
                        await self._progress.advance_docs()
                    window_sem.release()
                    if self._done_count >= self._total_docs:
                        self._all_done.set()
                    continue
                actor = DocumentActor(
                    ctx=ctx,
                    queue_map=self._queues,
                    available_stages=self._available_stages,
                    max_inflight_per_doc=self._configs[DocStage.TRANSLATING].workers * 2,
                    translator=self._translator,
                    group_builder=self._make_group_builder(),
                    finalize_fn=self._make_finalize_fn(
                        format_enabled=format_enabled,
                        debug_root=debug_root,
                        dump_protected=dump_protected,
                        dump_placeholders=dump_placeholders,
                        dump_nodes=dump_nodes,
                        dump_requests_log=dump_requests_log,
                    ),
                    on_done=self._make_done_callback(window_sem),
                    progress=self._progress,
                )
                self._actors[ctx.doc_id] = actor
                await actor.start(self._build_initial_group_tasks(ctx))

            await self._all_done.wait()
        finally:
            for stage, queue in self._queues.items():
                for _ in range(self._configs[stage].workers):
                    await queue.put(_SENTINEL)
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            dispatcher_task.cancel()
            try:
                await dispatcher_task
            except asyncio.CancelledError:
                pass
        return list(self._failed_files)

    async def _dispatcher(self) -> None:
        while True:
            event = await self._result_queue.get()
            actor = self._actors.get(event.doc_id)
            if actor is not None:
                await actor.on_completion(event)
            self._result_queue.task_done()

    async def _preprocess(
        self,
        *,
        path: Path,
        output_path: Path,
        fix_level: str,
        format_enabled: bool,
        request_log_enabled: bool,
    ) -> DocumentContext:
        text = path.read_text(encoding="utf-8")
        request_log: list[dict[str, Any]] | None = [] if request_log_enabled else None
        result = await self._translator.preprocess_document(
            text,
            fix_level=fix_level,
            format_enabled=format_enabled,
            request_log=request_log,
        )
        translated_nodes = {
            nid: Node(
                nid=nid,
                origin_text=node.origin_text,
                translated_text=node.translated_text,
            )
            for nid, node in result.nodes.items()
            if node.translated_text
        }
        return DocumentContext(
            doc_id=f"{path.stem}.{id(path)}",
            source_path=path,
            output_path=output_path,
            original_text=result.reference_text,
            protected_text=result.protected_text,
            segments=result.segments,
            nodes=result.nodes,
            store=result.placeholder_store,
            total_nodes=result.total_nodes,
            skip_count=result.skip_count,
            initial_groups_count=len(result.initial_groups),
            initial_groups=result.initial_groups,
            translated_nodes=translated_nodes,
            request_log=request_log or [],
        )

    def _build_initial_group_tasks(self, ctx: DocumentContext) -> list[GroupTask]:
        tasks: list[GroupTask] = []
        for i, group_text in enumerate(ctx.initial_groups):
            node_ids = tuple(
                int(match.group(1))
                for match in self._translator._rx_node_unpack.finditer(group_text)
            )
            tasks.append(
                GroupTask(
                    doc_id=ctx.doc_id,
                    stage=DocStage.TRANSLATING,
                    group_index=i,
                    node_ids=node_ids,
                    group_text=group_text,
                )
            )
        return tasks

    def _make_group_builder(self) -> GroupBuilder:
        def build(
            ctx: DocumentContext, failed_nodes: dict[int, Node], stage: DocStage
        ) -> list[GroupTask]:
            if not failed_nodes:
                return []
            cfg = self._configs[stage]
            max_chars = cfg.group_max_chars
            if max_chars is None:
                max_chars = self._translator.cfg.retry_group_max_chars or max(
                    1024, self._translator.cfg.max_chunk_chars // 2
                )
            groups = self._translator._group_nodes(
                failed_nodes,
                only_ids=sorted(failed_nodes.keys()),
                max_chunk_chars=max_chars,
                include_translated=True,
            )
            tasks: list[GroupTask] = []
            for i, group_text in enumerate(groups):
                node_ids = tuple(
                    int(match.group(1))
                    for match in self._translator._rx_node_unpack.finditer(group_text)
                )
                tasks.append(
                    GroupTask(
                        doc_id=ctx.doc_id,
                        stage=stage,
                        group_index=i,
                        node_ids=node_ids,
                        group_text=group_text,
                    )
                )
            return tasks

        return build

    def _make_finalize_fn(
        self,
        *,
        format_enabled: bool,
        debug_root: Path | None,
        dump_protected: bool,
        dump_placeholders: bool,
        dump_nodes: bool,
        dump_requests_log: bool,
    ) -> FinalizeFn:
        async def finalize(ctx: DocumentContext) -> None:
            for nid, node in ctx.translated_nodes.items():
                if nid in ctx.nodes:
                    ctx.nodes[nid].translated_text = node.translated_text
            for nid in self._translator._collect_failed_nodes(ctx.nodes):
                ctx.nodes[nid].translated_text = ctx.nodes[nid].origin_text
            result = await self._translator.finalize_document(
                reference_text=ctx.original_text,
                segments=ctx.segments,
                translated_nodes=ctx.nodes,
                store=ctx.store,
                format_enabled=format_enabled,
            )
            ctx.output_path.parent.mkdir(parents=True, exist_ok=True)
            ctx.output_path.write_text(result, encoding="utf-8")
            if debug_root is not None:
                debug_tag = f"{ctx.source_path.stem}.{short_hash(str(ctx.source_path))}"
                if dump_protected:
                    (debug_root / f"{debug_tag}.protected.md").write_text(
                        ctx.protected_text,
                        encoding="utf-8",
                    )
                if dump_placeholders:
                    ctx.store.save(str(debug_root / f"{debug_tag}.placeholders.json"))
                if dump_nodes:
                    node_payload = {
                        str(node_id): {
                            "origin_text": node.origin_text,
                            "translated_text": node.translated_text,
                        }
                        for node_id, node in ctx.nodes.items()
                    }
                    (debug_root / f"{debug_tag}.nodes.json").write_text(
                        json.dumps(node_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                if dump_requests_log:
                    (debug_root / f"{debug_tag}.requests.json").write_text(
                        json.dumps(ctx.request_log, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            failed = self._translator._collect_failed_nodes(ctx.translated_nodes)
            retry_groups = sum(
                count
                for stage, count in ctx.stage_group_counts.items()
                if stage != DocStage.TRANSLATING.value
            )
            logger.info(
                "Translated %s | nodes=%d ok=%d fail=%d skip=%d groups=%d retries=%d",
                ctx.source_path.name,
                ctx.total_nodes,
                max(ctx.total_nodes - len(failed), 0),
                len(failed),
                ctx.skip_count,
                ctx.initial_groups_count,
                retry_groups,
            )

        return finalize

    def _make_done_callback(self, window_sem: asyncio.Semaphore) -> DoneCallback:
        async def on_done(ctx: DocumentContext, err: Exception | None) -> None:
            if err is not None:
                logger.error("Failed %s: %s", ctx.source_path, err)
                self._failed_files.append(ctx.source_path)
            self._done_count += 1
            if self._progress is not None:
                await self._progress.advance_docs()
            window_sem.release()
            if self._done_count >= self._total_docs:
                self._all_done.set()

        return on_done

    async def _worker(self, queue: asyncio.Queue, config: QueueConfig) -> None:
        rotator = KeyRotator(config.api_keys) if config.api_keys else None
        while True:
            task = await queue.get()
            if task is _SENTINEL:
                queue.task_done()
                break
            try:
                while True:
                    route: RuntimeRoute | None = None
                    api_key: str | None = None
                    if config.route_pool is not None:
                        route = await config.route_pool.get()
                    elif rotator is not None:
                        api_key = await rotator.next_key()
                    actor = self._actors.get(task.doc_id)
                    req_log = actor.ctx.request_log if actor is not None else None
                    try:
                        async with config.provider_semaphore:
                            async with self._global_sem:
                                response = await self._translator._translate_group(
                                    task.group_text,
                                    config.provider,
                                    config.model,
                                    self._client,
                                    api_key,
                                    self._timeout,
                                    self._throttle,
                                    config.max_tokens,
                                    config.retry_limit,
                                    req_log,
                                    task.stage.value,
                                    task.group_index,
                                    None,
                                    route=route,
                                )
                        break
                    except ProviderError as exc:
                        if route is not None and config.route_pool is not None:
                            quota_hit = await config.route_pool.mark_quota_exceeded(
                                route, str(exc), exc.status_code
                            )
                            if quota_hit:
                                continue
                            if exc.retryable:
                                await config.route_pool.mark_error(route)
                        raise
                await self._result_queue.put(
                    CompletionEvent(
                        doc_id=task.doc_id,
                        stage=task.stage,
                        group_index=task.group_index,
                        node_ids=task.node_ids,
                        ok=True,
                        response=response,
                    )
                )
            except ProviderError:
                await self._result_queue.put(
                    CompletionEvent(
                        doc_id=task.doc_id,
                        stage=task.stage,
                        group_index=task.group_index,
                        node_ids=task.node_ids,
                        ok=False,
                        response="",
                    )
                )
            except Exception as exc:
                logger.error(
                    "Unexpected error in worker (doc=%s stage=%s group=%d): %s",
                    getattr(task, "doc_id", "?"),
                    getattr(getattr(task, "stage", None), "value", "?"),
                    getattr(task, "group_index", -1),
                    exc,
                )
                await self._result_queue.put(
                    CompletionEvent(
                        doc_id=task.doc_id,
                        stage=task.stage,
                        group_index=task.group_index,
                        node_ids=task.node_ids,
                        ok=False,
                        response="",
                    )
                )
            finally:
                queue.task_done()


__all__ = [
    "CompletionEvent",
    "DocStage",
    "DocumentActor",
    "DocumentContext",
    "GroupTask",
    "QueueConfig",
    "Scheduler",
    "StateError",
    "TRANSITIONS",
]
