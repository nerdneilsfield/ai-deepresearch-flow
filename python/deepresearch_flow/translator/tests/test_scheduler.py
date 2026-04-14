"""Black-box tests for scheduler state-model foundations."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass
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


class _ScriptedTranslator(_DummyTranslator):
    def __init__(self, stage_scripts: dict[str, dict[str, list[tuple[float, str]]]]) -> None:
        super().__init__({})
        self._stage_scripts = {
            doc_key: {stage: list(actions) for stage, actions in scripts.items()}
            for doc_key, scripts in stage_scripts.items()
        }
        self.active_docs = 0
        self.max_active_docs = 0
        self.call_log: list[tuple[str, str]] = []
        self._active_lock = asyncio.Lock()

    def _doc_key(self, group_text: str) -> str:
        match = self._rx.search(group_text)
        if match is None:
            return group_text
        return match.group(2)

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
        doc_key = self._doc_key(group_text)
        async with self._active_lock:
            self.active_docs += 1
            self.max_active_docs = max(self.max_active_docs, self.active_docs)
        try:
            self.call_log.append((doc_key, stage))
            actions = self._stage_scripts.get(doc_key, {}).get(stage, [])
            if actions:
                delay, output = actions.pop(0)
                if throttle is not None:
                    await throttle.tick()
                if delay:
                    await asyncio.sleep(delay)
                return output
            if throttle is not None:
                await throttle.tick()
            return ""
        finally:
            async with self._active_lock:
                self.active_docs -= 1


@dataclass(frozen=True)
class _FuzzDocPlan:
    stem: str
    source_text: str
    expected_output: str | None
    stage_scripts: dict[str, list[tuple[float, str]]]


@dataclass(frozen=True)
class _SchedulerFuzzCase:
    name: str
    document_window: int
    docs: tuple[_FuzzDocPlan, ...]


_FUZZ_MODES = ("direct", "retry", "fallback_1", "fallback_2", "fail")
_FUZZ_QUEUE_STAGES = (
    DocStage.TRANSLATING,
    DocStage.RETRYING,
    DocStage.FALLBACK_1,
    DocStage.FALLBACK_2,
)


def _node_response(payload: str) -> str:
    return f"<NODE_START_0000>\n{payload}\n</NODE_END_0000>\n"


def _deterministic_delay(case_index: int, doc_index: int, slot: int) -> float:
    step = (case_index * 7 + doc_index * 11 + slot * 13) % 5
    return round(0.002 + step * 0.004, 3)


def _build_doc_plan(case_index: int, doc_index: int, mode: str, prefix: str) -> _FuzzDocPlan:
    stem = f"{prefix}-{case_index:02d}-doc-{doc_index}"
    source_text = f"{stem} input"
    expected_output = f"{stem} {mode} output"
    direct_delay = _deterministic_delay(case_index, doc_index, 0)
    retry_delay = _deterministic_delay(case_index, doc_index, 1)
    fallback_1_delay = _deterministic_delay(case_index, doc_index, 2)
    fallback_2_delay = _deterministic_delay(case_index, doc_index, 3)

    if mode == "direct":
        stage_scripts = {
            "translating": [(direct_delay, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "retry":
        stage_scripts = {
            "translating": [(round(direct_delay + 0.012, 3), "")],
            "retrying": [(retry_delay, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "fallback_1":
        stage_scripts = {
            "translating": [(round(direct_delay + 0.012, 3), "")],
            "retrying": [(round(retry_delay + 0.008, 3), "")],
            "fallback_1": [(fallback_1_delay, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "fallback_2":
        stage_scripts = {
            "translating": [(round(direct_delay + 0.012, 3), "")],
            "retrying": [(round(retry_delay + 0.008, 3), "")],
            "fallback_1": [(round(fallback_1_delay + 0.006, 3), "")],
            "fallback_2": [(fallback_2_delay, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    stage_scripts = {
        "translating": [(round(direct_delay + 0.012, 3), "")],
        "retrying": [(round(retry_delay + 0.008, 3), "")],
        "fallback_1": [(round(fallback_1_delay + 0.006, 3), "")],
        "fallback_2": [(fallback_2_delay, "")],
    }
    return _FuzzDocPlan(stem, source_text, None, stage_scripts)


def _build_fuzz_case_set(
    *,
    prefix: str,
    case_count: int,
    document_count_fn,
    document_window_fn,
    mode_offset_fn,
) -> list[_SchedulerFuzzCase]:
    cases: list[_SchedulerFuzzCase] = []
    for case_index in range(case_count):
        docs = tuple(
            _build_doc_plan(
                case_index=case_index,
                doc_index=doc_index,
                mode=_FUZZ_MODES[(mode_offset_fn(case_index) + doc_index) % len(_FUZZ_MODES)],
                prefix=prefix,
            )
            for doc_index in range(document_count_fn(case_index))
        )
        cases.append(
            _SchedulerFuzzCase(
                name=f"{prefix}-{case_index:02d}",
                document_window=document_window_fn(case_index),
                docs=docs,
            )
        )
    return cases


_TIMING_FUZZ_CASES = _build_fuzz_case_set(
    prefix="timing",
    case_count=12,
    document_count_fn=lambda _case_index: 3,
    document_window_fn=lambda case_index: 2 + (case_index % 2),
    mode_offset_fn=lambda case_index: case_index,
)

_WINDOW_FUZZ_CASES = _build_fuzz_case_set(
    prefix="window",
    case_count=14,
    document_count_fn=lambda case_index: 5 + (case_index % 3),
    document_window_fn=lambda case_index: 1 + (case_index % 4),
    mode_offset_fn=lambda case_index: case_index * 2,
)

_DESTRUCTIVE_MODES = (
    "direct_fast",
    "direct_slow",
    "retry_fast",
    "retry_slow",
    "fallback_1_fast",
    "fallback_1_slow",
    "fallback_2_fast",
    "fallback_2_slow",
)


def _destructive_delay(case_index: int, doc_index: int, slot: int) -> float:
    step = (case_index * 11 + doc_index * 7 + slot * 5) % 9
    return round(0.001 + step * 0.004, 3)


def _build_destructive_doc_plan(case_index: int, doc_index: int, mode: str, prefix: str) -> _FuzzDocPlan:
    stem = f"{prefix}-{case_index:02d}-doc-{doc_index}"
    source_text = f"{stem} input"
    expected_output = f"{stem} {mode} output"
    translate_fast = _destructive_delay(case_index, doc_index, 0)
    translate_slow = round(translate_fast + 0.028, 3)
    retry_fast = _destructive_delay(case_index, doc_index, 1)
    retry_slow = round(retry_fast + 0.022, 3)
    fallback_1_fast = _destructive_delay(case_index, doc_index, 2)
    fallback_1_slow = round(fallback_1_fast + 0.018, 3)
    fallback_2_fast = _destructive_delay(case_index, doc_index, 3)
    fallback_2_slow = round(fallback_2_fast + 0.024, 3)

    if mode == "direct_fast":
        stage_scripts = {
            "translating": [(translate_fast, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "direct_slow":
        stage_scripts = {
            "translating": [(translate_slow, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "retry_fast":
        stage_scripts = {
            "translating": [(translate_slow, "")],
            "retrying": [(retry_fast, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "retry_slow":
        stage_scripts = {
            "translating": [(translate_fast, "")],
            "retrying": [(retry_slow, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "fallback_1_fast":
        stage_scripts = {
            "translating": [(translate_slow, "")],
            "retrying": [(retry_fast, "")],
            "fallback_1": [(fallback_1_fast, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "fallback_1_slow":
        stage_scripts = {
            "translating": [(translate_fast, "")],
            "retrying": [(retry_slow, "")],
            "fallback_1": [(fallback_1_slow, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    if mode == "fallback_2_fast":
        stage_scripts = {
            "translating": [(translate_slow, "")],
            "retrying": [(retry_fast, "")],
            "fallback_1": [(fallback_1_fast, "")],
            "fallback_2": [(fallback_2_fast, _node_response(expected_output))],
        }
        return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)

    stage_scripts = {
        "translating": [(translate_fast, "")],
        "retrying": [(retry_slow, "")],
        "fallback_1": [(fallback_1_slow, "")],
        "fallback_2": [(fallback_2_slow, _node_response(expected_output))],
    }
    return _FuzzDocPlan(stem, source_text, expected_output, stage_scripts)


def _build_destructive_case_set(prefix: str, case_count: int) -> list[_SchedulerFuzzCase]:
    cases: list[_SchedulerFuzzCase] = []
    for case_index in range(case_count):
        window = 1 + (case_index % 3)
        document_count = 6 + (case_index % 4) + (case_index // 10)
        mode_offset = case_index * 3
        docs = tuple(
            _build_destructive_doc_plan(
                case_index=case_index,
                doc_index=doc_index,
                mode=_DESTRUCTIVE_MODES[(mode_offset + doc_index) % len(_DESTRUCTIVE_MODES)],
                prefix=prefix,
            )
            for doc_index in range(document_count)
        )
        cases.append(
            _SchedulerFuzzCase(
                name=f"{prefix}-{case_index:02d}",
                document_window=window,
                docs=docs,
            )
        )
    return cases


_DESTRUCTIVE_FUZZ_CASES = _build_destructive_case_set(prefix="destructive", case_count=30)


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


def _make_fuzz_queue_configs() -> list[QueueConfig]:
    return [
        QueueConfig(
            stage=stage,
            workers=4,
            provider_semaphore=asyncio.Semaphore(4),
            route_pool=None,
            provider=object(),  # type: ignore[arg-type]
            model="dummy",
            api_keys=[],
            max_tokens=None,
            retry_limit=4,
        )
        for stage in _FUZZ_QUEUE_STAGES
    ]


async def _run_scheduler_fuzz_case(tmp_path: Path, case: _SchedulerFuzzCase) -> tuple[list[Path], int]:
    sources: dict[Path, Path] = {}
    expected_outputs: dict[Path, str] = {}
    expected_empty_outputs: set[Path] = set()
    stage_scripts: dict[str, dict[str, list[tuple[float, str]]]] = {}

    for doc in case.docs:
        source = tmp_path / f"{doc.stem}.md"
        output = tmp_path / f"{doc.stem}.zh.md"
        source.write_text(doc.source_text, encoding="utf-8")
        sources[source] = output
        if doc.expected_output is not None:
            expected_outputs[output] = doc.expected_output
        else:
            expected_empty_outputs.add(output)
        stage_scripts[doc.source_text] = {
            stage: list(actions) for stage, actions in doc.stage_scripts.items()
        }

    translator = _ScriptedTranslator(stage_scripts)
    scheduler = Scheduler(
        translator=translator,  # type: ignore[arg-type]
        document_window=case.document_window,
        global_semaphore=asyncio.Semaphore(100),
        queue_configs=_make_fuzz_queue_configs(),
        progress=None,
        client=httpx.AsyncClient(),
        throttle=None,
        timeout=10.0,
    )
    try:
        failed = await asyncio.wait_for(
            scheduler.run(
                paths=list(sources),
                output_map=sources,
                fix_level="off",
                format_enabled=False,
                request_log_enabled=False,
            ),
            timeout=10.0,
        )
    finally:
        await scheduler._client.aclose()

    assert failed == []
    assert translator.max_active_docs <= case.document_window
    for output_path, expected_text in expected_outputs.items():
        assert output_path.read_text(encoding="utf-8") == expected_text
    observed_empty_outputs = {
        output_path
        for output_path in sources.values()
        if not output_path.exists() or output_path.read_text(encoding="utf-8") == ""
    }
    assert observed_empty_outputs == expected_empty_outputs
    return [], translator.max_active_docs


async def _run_scheduler_fuzz_case_twice(
    tmp_path: Path, case: _SchedulerFuzzCase
) -> tuple[dict[str, str], dict[str, str], list[Path], list[Path], int, int]:
    async def run_once(run_root: Path) -> tuple[dict[str, str], list[Path], int]:
        run_root.mkdir(parents=True, exist_ok=True)
        sources: dict[Path, Path] = {}
        expected_outputs: dict[Path, str] = {}
        stage_scripts: dict[str, dict[str, list[tuple[float, str]]]] = {}

        for doc in case.docs:
            source = run_root / f"{doc.stem}.md"
            output = run_root / f"{doc.stem}.zh.md"
            source.write_text(doc.source_text, encoding="utf-8")
            sources[source] = output
            expected_outputs[output] = doc.expected_output or ""
            stage_scripts[doc.source_text] = {
                stage: list(actions) for stage, actions in doc.stage_scripts.items()
            }

        translator = _ScriptedTranslator(stage_scripts)
        scheduler = Scheduler(
            translator=translator,  # type: ignore[arg-type]
            document_window=case.document_window,
            global_semaphore=asyncio.Semaphore(100),
            queue_configs=_make_fuzz_queue_configs(),
            progress=None,
            client=httpx.AsyncClient(),
            throttle=None,
            timeout=10.0,
        )
        try:
            failed = await asyncio.wait_for(
                scheduler.run(
                    paths=list(sources),
                    output_map=sources,
                    fix_level="off",
                    format_enabled=False,
                    request_log_enabled=False,
                ),
                timeout=10.0,
            )
        finally:
            await scheduler._client.aclose()

        observed_outputs = {
            output_path.name: output_path.read_text(encoding="utf-8")
            for output_path in sources.values()
        }
        assert failed == []
        assert translator.max_active_docs <= case.document_window
        assert observed_outputs == {path.name: text for path, text in expected_outputs.items()}
        return observed_outputs, failed, translator.max_active_docs

    first_outputs, first_failed, first_max_active_docs = await run_once(tmp_path / "first")
    second_outputs, second_failed, second_max_active_docs = await run_once(tmp_path / "second")
    return (
        first_outputs,
        second_outputs,
        first_failed,
        second_failed,
        first_max_active_docs,
        second_max_active_docs,
    )


@pytest.mark.parametrize("case", _TIMING_FUZZ_CASES, ids=lambda case: case.name)
def test_scheduler_timing_fuzz(tmp_path: Path, case: _SchedulerFuzzCase) -> None:
    asyncio.run(_run_scheduler_fuzz_case(tmp_path, case))


@pytest.mark.parametrize("case", _WINDOW_FUZZ_CASES, ids=lambda case: case.name)
def test_scheduler_document_window_fuzz(tmp_path: Path, case: _SchedulerFuzzCase) -> None:
    asyncio.run(_run_scheduler_fuzz_case(tmp_path, case))


@pytest.mark.parametrize("case", _DESTRUCTIVE_FUZZ_CASES, ids=lambda case: case.name)
def test_scheduler_destructive_window_timing_fuzz(tmp_path: Path, case: _SchedulerFuzzCase) -> None:
    async def run() -> None:
        first_outputs, second_outputs, first_failed, second_failed, first_max_active_docs, second_max_active_docs = (
            await _run_scheduler_fuzz_case_twice(tmp_path, case)
        )
        assert first_outputs == second_outputs
        assert first_failed == []
        assert second_failed == []
        assert first_max_active_docs <= case.document_window
        assert second_max_active_docs <= case.document_window

    asyncio.run(run())


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
