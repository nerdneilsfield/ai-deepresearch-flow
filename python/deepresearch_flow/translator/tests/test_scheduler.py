"""Black-box tests for scheduler state-model foundations."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from pathlib import Path
import re
from types import SimpleNamespace

import httpx
import pytest

from deepresearch_flow.translator.engine import RequestThrottle
from deepresearch_flow.translator.placeholder import PlaceHolderStore
from deepresearch_flow.translator.scheduler import (
    CompletionEvent,
    DocStage,
    DocumentActor,
    DocumentContext,
    GroupTask,
    QueueConfig,
    Scheduler,
    StateError,
    TRANSITIONS,
)
from deepresearch_flow.translator.segment import Node, Segment


def _make_context() -> DocumentContext:
    return DocumentContext(
        doc_id="doc-1",
        source_path=Path("/tmp/source.md"),
        output_path=Path("/tmp/source.zh.md"),
        original_text="original text",
        protected_text="protected text",
        segments=[Segment(kind="nodes", content=[0])],
        nodes={0: Node(nid=0, origin_text="hello")},
        store=PlaceHolderStore(),
        total_nodes=1,
        skip_count=0,
        initial_groups_count=1,
    )


def test_docstage_transition_map() -> None:
    assert TRANSITIONS[DocStage.PREPROCESSED] == [DocStage.TRANSLATING]
    assert TRANSITIONS[DocStage.TRANSLATING] == [
        DocStage.RETRYING,
        DocStage.FINALIZING,
    ]
    assert TRANSITIONS[DocStage.RETRYING] == [
        DocStage.FALLBACK_1,
        DocStage.FINALIZING,
    ]
    assert TRANSITIONS[DocStage.FALLBACK_1] == [
        DocStage.FALLBACK_2,
        DocStage.FINALIZING,
    ]
    assert TRANSITIONS[DocStage.FALLBACK_2] == [DocStage.FINALIZING]
    assert TRANSITIONS[DocStage.FINALIZING] == [DocStage.DONE]
    assert DocStage.DONE not in TRANSITIONS


def test_docstage_transitions_are_forward_only() -> None:
    order = list(DocStage)
    for idx, stage in enumerate(order):
        for target in TRANSITIONS.get(stage, []):
            assert order.index(target) > idx


def test_document_context_keeps_preprocess_fields_and_own_request_log() -> None:
    ctx1 = _make_context()
    ctx2 = _make_context()

    assert ctx1.stage is DocStage.PREPROCESSED
    assert ctx1.total_nodes == 1
    assert ctx1.skip_count == 0
    assert ctx1.initial_groups_count == 1
    assert ctx1.translated_nodes == {}
    assert ctx1.pending_counts == {}
    assert ctx1.stage_group_counts == {}
    assert ctx1.retry_rounds == 0
    assert ctx1.request_log == []

    ctx1.request_log.append({"event": "first"})
    assert ctx2.request_log == []


def test_group_task_is_frozen_and_records_fields() -> None:
    task = GroupTask(
        doc_id="doc-1",
        stage=DocStage.TRANSLATING,
        group_index=3,
        node_ids=(1, 2),
        group_text="group payload",
    )

    assert task.doc_id == "doc-1"
    assert task.stage is DocStage.TRANSLATING
    assert task.group_index == 3
    assert task.node_ids == (1, 2)
    assert task.group_text == "group payload"
    with pytest.raises(FrozenInstanceError):
        task.group_text = "changed"  # type: ignore[misc]


def test_completion_event_is_frozen_and_records_fields() -> None:
    event = CompletionEvent(
        doc_id="doc-1",
        stage=DocStage.RETRYING,
        group_index=1,
        node_ids=(4,),
        ok=False,
        response="",
    )

    assert event.doc_id == "doc-1"
    assert event.stage is DocStage.RETRYING
    assert event.group_index == 1
    assert event.node_ids == (4,)
    assert event.ok is False
    assert event.response == ""
    with pytest.raises(FrozenInstanceError):
        event.ok = True  # type: ignore[misc]


def test_state_error_is_raised_as_a_domain_error() -> None:
    err = StateError("invalid transition")
    assert isinstance(err, Exception)
    assert str(err) == "invalid transition"


def test_document_actor_start_rejects_invalid_stage() -> None:
    async def run() -> None:
        queue_map = {DocStage.TRANSLATING: asyncio.Queue()}
        ctx = _make_context()
        ctx.stage = DocStage.DONE
        actor = DocumentActor(
            ctx=ctx,
            queue_map=queue_map,
            available_stages=[DocStage.TRANSLATING],
            max_inflight_per_doc=1,
            translator=SimpleNamespace(),  # type: ignore[arg-type]
            group_builder=lambda _ctx, _failed, _stage: [],
            finalize_fn=lambda _ctx: asyncio.sleep(0),
            on_done=lambda _ctx, _err: asyncio.sleep(0),
            progress=None,
        )
        with pytest.raises(StateError):
            await actor.start([])

    asyncio.run(run())


def test_document_actor_ignores_stale_completion_event() -> None:
    async def run() -> None:
        queue = asyncio.Queue()
        ctx = _make_context()
        ctx.stage = DocStage.RETRYING
        ctx.pending_counts[DocStage.TRANSLATING.value] = 1
        actor = DocumentActor(
            ctx=ctx,
            queue_map={DocStage.RETRYING: queue},
            available_stages=[DocStage.TRANSLATING, DocStage.RETRYING],
            max_inflight_per_doc=1,
            translator=_DummyTranslator({}),
            group_builder=lambda _ctx, _failed, _stage: [],
            finalize_fn=lambda _ctx: asyncio.sleep(0),
            on_done=lambda _ctx, _err: asyncio.sleep(0),
            progress=None,
        )
        await actor.on_completion(
            CompletionEvent(
                doc_id=ctx.doc_id,
                stage=DocStage.TRANSLATING,
                group_index=0,
                node_ids=(0,),
                ok=True,
                response="<NODE_START_0000>\nlate\n</NODE_END_0000>\n",
            )
        )
        assert ctx.pending_counts[DocStage.TRANSLATING.value] == 1
        assert ctx.translated_nodes == {}

    asyncio.run(run())


def test_document_actor_does_not_increment_retry_rounds_for_empty_groups() -> None:
    done: list[tuple[DocumentContext, Exception | None]] = []

    async def finalize(ctx: DocumentContext) -> None:
        ctx.nodes[0].translated_text = "hello"

    async def on_done(ctx: DocumentContext, err: Exception | None) -> None:
        done.append((ctx, err))

    async def run() -> None:
        queue = asyncio.Queue()
        ctx = _make_context()
        ctx.translated_nodes[0] = Node(nid=0, origin_text="hello", translated_text="")
        actor = DocumentActor(
            ctx=ctx,
            queue_map={DocStage.TRANSLATING: queue, DocStage.RETRYING: queue},
            available_stages=[DocStage.TRANSLATING, DocStage.RETRYING],
            max_inflight_per_doc=1,
            translator=_DummyTranslator({}),
            group_builder=lambda _ctx, _failed, _stage: [],
            finalize_fn=finalize,
            on_done=on_done,
            progress=None,
        )
        await actor.start([])
        assert ctx.retry_rounds == 0
        assert ctx.stage is DocStage.DONE
        assert done and done[0][1] is None

    asyncio.run(run())


class _DummyTranslator:
    def __init__(self, stage_outputs: dict[str, list[str]]) -> None:
        self.cfg = SimpleNamespace(retry_group_max_chars=None, max_chunk_chars=4000)
        self._stage_outputs = {stage: list(values) for stage, values in stage_outputs.items()}
        self._rx = re.compile(
            r"<NODE_START_(\d+)>\n(.*?)\n</NODE_END_\1>\n?",
            re.DOTALL,
        )
        self._rx_node_unpack = self._rx

    async def preprocess_document(
        self,
        text: str,
        fix_level: str,
        format_enabled: bool,
        request_log=None,
    ):
        _ = (fix_level, format_enabled, request_log)
        node = Node(nid=0, origin_text=text, translated_text="")
        return SimpleNamespace(
            reference_text=text,
            protected_text=text,
            placeholder_store=PlaceHolderStore(),
            segments=[Segment(kind="nodes", content=[0])],
            nodes={0: node},
            total_nodes=1,
            skip_count=0,
            initial_groups=[f"<NODE_START_0000>\n{text}\n</NODE_END_0000>\n"],
        )

    async def finalize_document(
        self,
        reference_text: str,
        segments,
        translated_nodes,
        store,
        format_enabled: bool,
    ) -> str:
        _ = (reference_text, segments, store, format_enabled)
        return translated_nodes[0].translated_text

    def _group_nodes(
        self,
        failed_nodes: dict[int, Node],
        only_ids=None,
        max_chunk_chars=None,
        include_translated: bool = False,
    ) -> list[str]:
        _ = (max_chunk_chars, include_translated)
        ids = only_ids if only_ids is not None else sorted(failed_nodes)
        return [
            f"<NODE_START_{nid:04d}>\n{failed_nodes[nid].origin_text}\n</NODE_END_{nid:04d}>\n"
            for nid in ids
        ]

    def _ungroup_nodes(self, group_text: str, origin_nodes: dict[int, Node]) -> dict[int, Node]:
        out: dict[int, Node] = {}
        for match in self._rx.finditer(group_text):
            nid = int(match.group(1))
            out[nid] = Node(
                nid=nid,
                origin_text=origin_nodes[nid].origin_text,
                translated_text=match.group(2),
            )
        return out

    def _fix_placeholder_typos(self, text: str, valid_placeholders: set[str]) -> str:
        _ = valid_placeholders
        return text

    def _align_placeholders(self, orig: str, trans: str) -> str:
        _ = orig
        return trans

    def _collect_failed_nodes(self, nodes: dict[int, Node]) -> dict[int, Node]:
        return {
            nid: node
            for nid, node in nodes.items()
            if not node.translated_text
        }

    async def _translate_group(
        self,
        group_text: str,
        provider,
        model,
        client,
        api_key,
        timeout,
        throttle: RequestThrottle | None,
        max_tokens,
        max_retries,
        request_log,
        stage: str,
        group_index: int,
        dump_callback,
        route=None,
    ) -> str:
        _ = (
            group_text,
            provider,
            model,
            client,
            api_key,
            timeout,
            max_tokens,
            max_retries,
            request_log,
            group_index,
            dump_callback,
            route,
        )
        if throttle is not None:
            await throttle.tick()
        outputs = self._stage_outputs.get(stage, [])
        return outputs.pop(0) if outputs else ""


def _make_queue_config(stage: DocStage) -> QueueConfig:
    return QueueConfig(
        stage=stage,
        workers=1,
        provider_semaphore=asyncio.Semaphore(1),
        route_pool=None,
        provider=object(),  # type: ignore[arg-type]
        model="dummy",
        api_keys=[],
        max_tokens=None,
        retry_limit=1,
    )


def test_scheduler_writes_output_for_single_document(tmp_path: Path) -> None:
    async def run() -> None:
        source = tmp_path / "doc.md"
        output = tmp_path / "doc.zh.md"
        source.write_text("hello", encoding="utf-8")
        translator = _DummyTranslator(
            {
                "translating": ["<NODE_START_0000>\n你好\n</NODE_END_0000>\n"],
            }
        )
        scheduler = Scheduler(
            translator=translator,  # type: ignore[arg-type]
            document_window=1,
            global_semaphore=asyncio.Semaphore(1),
            queue_configs=[_make_queue_config(DocStage.TRANSLATING)],
            progress=None,
            client=httpx.AsyncClient(),
            throttle=None,
            timeout=10.0,
        )
        try:
            failed = await scheduler.run(
                paths=[source],
                output_map={source: output},
                fix_level="off",
                format_enabled=False,
                request_log_enabled=False,
            )
        finally:
            await scheduler._client.aclose()
        assert failed == []
        assert output.read_text(encoding="utf-8") == "你好"

    asyncio.run(run())


def test_scheduler_retries_failed_initial_group(tmp_path: Path) -> None:
    async def run() -> None:
        source = tmp_path / "doc.md"
        output = tmp_path / "doc.zh.md"
        source.write_text("hello", encoding="utf-8")
        translator = _DummyTranslator(
            {
                "translating": [""],
                "retrying": ["<NODE_START_0000>\n你好\n</NODE_END_0000>\n"],
            }
        )
        scheduler = Scheduler(
            translator=translator,  # type: ignore[arg-type]
            document_window=1,
            global_semaphore=asyncio.Semaphore(1),
            queue_configs=[
                _make_queue_config(DocStage.TRANSLATING),
                _make_queue_config(DocStage.RETRYING),
            ],
            progress=None,
            client=httpx.AsyncClient(),
            throttle=None,
            timeout=10.0,
        )
        try:
            failed = await scheduler.run(
                paths=[source],
                output_map={source: output},
                fix_level="off",
                format_enabled=False,
                request_log_enabled=False,
            )
        finally:
            await scheduler._client.aclose()
        assert failed == []
        assert output.read_text(encoding="utf-8") == "你好"

    asyncio.run(run())
