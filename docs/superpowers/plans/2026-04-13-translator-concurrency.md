# Translator Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the serial document-processing loop with a multi-document, multi-queue concurrent scheduler that separates initial translation from retry/fallback stages.

**Architecture:** Extract `preprocess_document()` and `finalize_document()` from `engine.py`'s `translate()`. Build a new `scheduler.py` with a `DocStage` state machine, `DocumentActor` (single-writer per document), 4 queues (initial/retry/fallback/fallback_2), and worker pools. Rewire CLI to use the scheduler. Add per-stage progress bars.

**Tech Stack:** Python 3.14, asyncio, httpx, tqdm, click, pytest

---

## Scope

This plan covers:

- `python/deepresearch_flow/translator/engine.py`
- `python/deepresearch_flow/translator/scheduler.py` (new)
- `python/deepresearch_flow/translator/progress.py` (new)
- `python/deepresearch_flow/translator/cli.py`
- `python/deepresearch_flow/translator/tests/test_scheduler.py` (new)
- `python/deepresearch_flow/translator/tests/test_progress.py` (new)

This plan does not cover:

- `protector.py`, `placeholder.py`, `segment.py`, `fixers.py`, `prompts.py`, `config.py`
- `paper/routing.py`, `paper/llm.py`, `paper/providers/*`
- Translation quality, prompt engineering, or content policy
- Per-attempt `dump_callback` in scheduler workers (intentional scope cut; `request_log` is preserved, staged debug dumps are deferred to a future iteration)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/translator/engine.py` | Modify | Extract `preprocess_document()`, `finalize_document()`, update `_translate_group()` signature; retain `translate()` as compat wrapper |
| `python/deepresearch_flow/translator/scheduler.py` | Create | `DocStage`, `TRANSITIONS`, `StateError`, `DocumentContext`, `GroupTask`, `CompletionEvent`, `QueueConfig`, `DocumentActor`, `Scheduler` |
| `python/deepresearch_flow/translator/progress.py` | Create | `ProgressReporter` with per-stage tqdm bars |
| `python/deepresearch_flow/translator/cli.py` | Modify | New CLI params, deprecate `--group-concurrency`, wire `Scheduler` |
| `python/deepresearch_flow/translator/tests/test_scheduler.py` | Create | State machine tests, DocumentActor tests, Scheduler integration tests |
| `python/deepresearch_flow/translator/tests/test_progress.py` | Create | ProgressReporter tests |

---

## Task 1: Extract `preprocess_document()` and `finalize_document()` from `engine.py`

**Files:**
- Modify: `python/deepresearch_flow/translator/engine.py:669-1148`

- [ ] **Step 1: Write `preprocess_document()` method**

Extract the first half of `translate()` (lines 698–729) into a new public method on `MarkdownTranslator`. This includes fix, format, protect, split, skip placeholder-only nodes, and group:

```python
@dataclass
class PreprocessResult:
    original_text: str
    protected_text: str
    segments: list
    nodes: dict[int, Node]
    store: PlaceHolderStore
    initial_groups: list[str]
    skip_count: int
    total_nodes: int

async def preprocess_document(
    self,
    text: str,
    fix_level: str,
    format_enabled: bool,
    dump_callback: Callable[[DumpSnapshot], None] | None = None,
    request_log: list[dict[str, Any]] | None = None,
) -> PreprocessResult:
    """Extract the preprocessing phase from translate().

    Returns a PreprocessResult with all data needed by DocumentContext.
    """
    original_text = text
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
        logger.debug("Nodes: %d", total_nodes)

    skip_count = 0
    for node in nodes.values():
        if self._is_placeholder_only(node.origin_text):
            node.translated_text = node.origin_text
            skip_count += 1
    if skip_count:
        logger.debug("Skipped %d placeholder-only nodes", skip_count)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Placeholder counts: %s", store.kind_counts())

    groups = self._group_nodes(nodes)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Groups: %d", len(groups))

    return PreprocessResult(
        original_text=original_text,
        protected_text=protected,
        segments=segments,
        nodes=nodes,
        store=store,
        initial_groups=groups,
        skip_count=skip_count,
        total_nodes=total_nodes,
    )
```

- [ ] **Step 2: Write `finalize_document()` method**

Extract the tail of `translate()` (lines 1128–1147) into a new public method:

```python
async def finalize_document(
    self,
    original_text: str,
    protected_text: str,
    segments: list,
    translated_nodes: dict[int, Node],
    store: PlaceHolderStore,
    format_enabled: bool,
) -> str:
    """Reassemble, format, and restore protected text."""
    # Failed nodes fall back to origin text
    for nid, node in translated_nodes.items():
        if not self._is_translation_success(node.origin_text, node.translated_text):
            node.translated_text = node.origin_text

    merged_text = reassemble_segments(segments, translated_nodes)
    if format_enabled:
        formatted = await self._format_markdown(merged_text, "post")
        merged_text = preserve_heading_levels(original_text, formatted)
    else:
        merged_text = preserve_heading_levels(original_text, merged_text)
    merged_text = self._normalize_markdown_blocks(merged_text)
    restored = self._restore_protected_text(merged_text, store)
    return restored
```

- [ ] **Step 3: Update `_translate_group()` signature**

Remove `semaphore` and `route_pool` parameters. Add `route: RuntimeRoute | None`. The caller is now responsible for route acquisition and semaphore management.

In `_translate_group()` (line 556), change the signature from:

```python
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
    route_pool: RoutePool | None = None,
) -> str:
```

to:

```python
async def _translate_group(
    self,
    group_text: str,
    provider: ProviderConfig,
    model: str,
    client: httpx.AsyncClient,
    api_key: str | None,
    timeout: float,
    throttle: RequestThrottle | None,
    max_tokens: int | None,
    max_retries: int,
    request_log: list[dict[str, Any]] | None,
    stage: str,
    group_index: int,
    dump_callback: Callable[[DumpSnapshot], None] | None,
    route: RuntimeRoute | None = None,
) -> str:
```

Inside the method body, replace:

```python
if route_pool is not None:
    route = await route_pool.get()
    current_provider = route.provider
    current_model = route.model.model_name
    current_api_key = route.key.value
```

with:

```python
if route is not None:
    current_provider = route.provider
    current_model = route.model.model_name
    current_api_key = route.key.value
```

Remove the `async with semaphore:` wrapper around the `call_provider` call (the caller now acquires semaphores). Remove the `route_pool.mark_quota_exceeded()` and `route_pool.mark_error()` calls from the except block (the caller now handles route lifecycle).

- [ ] **Step 4: Rewrite `translate()` to call extracted methods**

Replace the inline code in `translate()` with calls to `preprocess_document()` and `finalize_document()`. The middle section (run_groups + retry/fallback loops) stays as-is for now — it's the compatibility wrapper. Update the `run_groups` inner function to handle the new `_translate_group()` signature: acquire route before semaphore, handle route error marking.

- [ ] **Step 5: Run existing tests**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/ -q
```

Expected: PASS — no behavior change, only extraction.

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/translator/engine.py
git commit -m "refactor(translator): extract preprocess_document and finalize_document from translate()"
```

---

## Task 2: Build State Machine and Data Model

**Files:**
- Create: `python/deepresearch_flow/translator/scheduler.py`
- Create: `python/deepresearch_flow/translator/tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests for state machine**

Create `python/deepresearch_flow/translator/tests/test_scheduler.py`:

```python
"""Black-box tests for DocStage state machine transitions."""

import pytest
from deepresearch_flow.translator.scheduler import (
    DocStage,
    StateError,
    TRANSITIONS,
)


def test_preprocessed_can_transition_to_translating():
    assert DocStage.TRANSLATING in TRANSITIONS[DocStage.PREPROCESSED]


def test_translating_can_transition_to_retrying_or_finalizing():
    allowed = TRANSITIONS[DocStage.TRANSLATING]
    assert DocStage.RETRYING in allowed
    assert DocStage.FINALIZING in allowed


def test_retrying_can_transition_to_fallback_1_or_finalizing():
    allowed = TRANSITIONS[DocStage.RETRYING]
    assert DocStage.FALLBACK_1 in allowed
    assert DocStage.FINALIZING in allowed


def test_fallback_1_can_transition_to_fallback_2_or_finalizing():
    allowed = TRANSITIONS[DocStage.FALLBACK_1]
    assert DocStage.FALLBACK_2 in allowed
    assert DocStage.FINALIZING in allowed


def test_fallback_2_can_only_transition_to_finalizing():
    allowed = TRANSITIONS[DocStage.FALLBACK_2]
    assert allowed == [DocStage.FINALIZING]


def test_finalizing_can_only_transition_to_done():
    allowed = TRANSITIONS[DocStage.FINALIZING]
    assert allowed == [DocStage.DONE]


def test_done_has_no_transitions():
    assert DocStage.DONE not in TRANSITIONS


def test_backward_transition_is_invalid():
    """No state should allow transitioning to a previous state."""
    order = list(DocStage)
    for i, stage in enumerate(order):
        if stage not in TRANSITIONS:
            continue
        for allowed in TRANSITIONS[stage]:
            assert order.index(allowed) > i, (
                f"{stage} allows backward transition to {allowed}"
            )
```

- [ ] **Step 2: Run tests, confirm they fail**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_scheduler.py -q
```

Expected: FAIL — `scheduler` module does not exist.

- [ ] **Step 3: Implement state machine and data model**

Create `python/deepresearch_flow/translator/scheduler.py` with the state machine, data classes, and `StateError`:

```python
"""Multi-document concurrent translation scheduler."""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from deepresearch_flow.translator.placeholder import PlaceHolderStore
from deepresearch_flow.translator.segment import Node, Segment


class StateError(Exception):
    """Raised when an invalid state transition is attempted."""


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


# Stage ordering for determining next available stage
STAGE_ORDER: list[DocStage] = [
    DocStage.TRANSLATING,
    DocStage.RETRYING,
    DocStage.FALLBACK_1,
    DocStage.FALLBACK_2,
]


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

    # Preprocess outputs (immutable after creation)
    original_text: str
    protected_text: str
    segments: list[Segment]
    nodes: dict[int, Node]
    store: PlaceHolderStore

    # Preprocess stats (immutable after creation)
    total_nodes: int = 0
    skip_count: int = 0
    initial_groups_count: int = 0

    # Translation state (written only by DocumentActor)
    translated_nodes: dict[int, Node] = field(default_factory=dict)
    pending_counts: dict[str, int] = field(default_factory=dict)
    stage: DocStage = DocStage.PREPROCESSED
    # Per-stage group counts for final stats/logging
    stage_group_counts: dict[str, int] = field(default_factory=dict)
    retry_rounds: int = 0

    # Debug / stats
    request_log: list[dict[str, Any]] | None = None
```

- [ ] **Step 4: Run tests, confirm they pass**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_scheduler.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/translator/scheduler.py python/deepresearch_flow/translator/tests/test_scheduler.py
git commit -m "feat(translator): add DocStage state machine and data model"
```

---

## Task 3: Build DocumentActor

**Files:**
- Modify: `python/deepresearch_flow/translator/scheduler.py`
- Modify: `python/deepresearch_flow/translator/tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests for DocumentActor**

Add to `test_scheduler.py`:

```python
import asyncio
from unittest.mock import AsyncMock
from deepresearch_flow.translator.scheduler import (
    DocumentActor,
    DocumentContext,
    CompletionEvent,
    GroupTask,
    DocStage,
)
from deepresearch_flow.translator.segment import Node, Segment
from deepresearch_flow.translator.placeholder import PlaceHolderStore


def _make_ctx(
    num_nodes: int = 3,
    doc_id: str = "doc-1",
) -> DocumentContext:
    """Build a minimal DocumentContext for testing."""
    nodes = {
        i: Node(nid=i, origin_text=f"text-{i}") for i in range(num_nodes)
    }
    segments = [Segment(kind="nodes", content=list(range(num_nodes)))]
    return DocumentContext(
        doc_id=doc_id,
        source_path=Path("/tmp/test.md"),
        output_path=Path("/tmp/test.zh.md"),
        original_text="original",
        protected_text="protected",
        segments=segments,
        nodes=nodes,
        store=PlaceHolderStore(),
    )


def _make_groups(ctx: DocumentContext) -> list[GroupTask]:
    """Build one GroupTask per node."""
    return [
        GroupTask(
            doc_id=ctx.doc_id,
            stage=DocStage.TRANSLATING,
            group_index=i,
            node_ids=(i,),
            group_text=f"text-{i}",
        )
        for i in ctx.nodes
    ]


@pytest.mark.asyncio
async def test_actor_start_transitions_to_translating():
    ctx = _make_ctx(num_nodes=2)
    queue = asyncio.Queue()
    queues = {DocStage.TRANSLATING: queue}
    actor = DocumentActor(
        ctx=ctx,
        queue_map=queues,
        available_stages=[DocStage.TRANSLATING],
        max_inflight_per_doc=10,
        translator=None,
        group_nodes_fn=lambda nodes, **kw: [
            (tuple(nodes.keys()), "grouped")
        ],
    )
    groups = _make_groups(ctx)
    await actor.start(groups)
    assert ctx.stage == DocStage.TRANSLATING
    assert not queue.empty()


@pytest.mark.asyncio
async def test_actor_all_success_goes_to_finalizing():
    ctx = _make_ctx(num_nodes=2)
    queue = asyncio.Queue()
    queues = {DocStage.TRANSLATING: queue}
    finalize_called = []

    actor = DocumentActor(
        ctx=ctx,
        queue_map=queues,
        available_stages=[DocStage.TRANSLATING],
        max_inflight_per_doc=10,
        translator=None,
        group_nodes_fn=lambda nodes, **kw: [],
        finalize_fn=AsyncMock(side_effect=lambda c: finalize_called.append(True)),
    )
    groups = _make_groups(ctx)
    await actor.start(groups)

    # Simulate all completions succeeding
    for i in range(2):
        event = CompletionEvent(
            doc_id="doc-1", stage=DocStage.TRANSLATING,
            group_index=i, node_ids=(i,),
            ok=True, response=f"translated-{i}",
        )
        await actor.on_completion(event)

    assert ctx.stage == DocStage.DONE


@pytest.mark.asyncio
async def test_actor_failure_advances_to_retry():
    ctx = _make_ctx(num_nodes=2)
    initial_q = asyncio.Queue()
    retry_q = asyncio.Queue()
    queues = {
        DocStage.TRANSLATING: initial_q,
        DocStage.RETRYING: retry_q,
    }

    actor = DocumentActor(
        ctx=ctx,
        queue_map=queues,
        available_stages=[DocStage.TRANSLATING, DocStage.RETRYING],
        max_inflight_per_doc=10,
        translator=None,
        group_nodes_fn=lambda nodes, **kw: [
            (tuple(nodes.keys()), "retry-grouped")
        ],
    )
    groups = _make_groups(ctx)
    await actor.start(groups)

    # Node 0 succeeds, node 1 fails
    await actor.on_completion(CompletionEvent(
        doc_id="doc-1", stage=DocStage.TRANSLATING,
        group_index=0, node_ids=(0,), ok=True, response="translated-0",
    ))
    await actor.on_completion(CompletionEvent(
        doc_id="doc-1", stage=DocStage.TRANSLATING,
        group_index=1, node_ids=(1,), ok=False, response="",
    ))

    assert ctx.stage == DocStage.RETRYING
    assert not retry_q.empty()


@pytest.mark.asyncio
async def test_actor_emission_credit_limits_enqueue():
    ctx = _make_ctx(num_nodes=5)
    queue = asyncio.Queue()
    queues = {DocStage.TRANSLATING: queue}
    actor = DocumentActor(
        ctx=ctx,
        queue_map=queues,
        available_stages=[DocStage.TRANSLATING],
        max_inflight_per_doc=2,  # Only 2 at a time
        translator=None,
        group_nodes_fn=lambda nodes, **kw: [],
    )
    groups = _make_groups(ctx)
    await actor.start(groups)

    # Should only enqueue 2, not 5
    assert queue.qsize() == 2
    # Total pending is still 5
    assert ctx.pending_counts[DocStage.TRANSLATING.value] == 5


@pytest.mark.asyncio
async def test_actor_credit_refill_on_completion():
    ctx = _make_ctx(num_nodes=4)
    queue = asyncio.Queue()
    queues = {DocStage.TRANSLATING: queue}
    actor = DocumentActor(
        ctx=ctx,
        queue_map=queues,
        available_stages=[DocStage.TRANSLATING],
        max_inflight_per_doc=2,
        translator=None,
        group_nodes_fn=lambda nodes, **kw: [],
    )
    groups = _make_groups(ctx)
    await actor.start(groups)
    assert queue.qsize() == 2

    # Drain queue and complete first task
    task0 = await queue.get()
    await actor.on_completion(CompletionEvent(
        doc_id="doc-1", stage=DocStage.TRANSLATING,
        group_index=task0.group_index, node_ids=task0.node_ids,
        ok=True, response="translated",
    ))
    # Credit refill should have enqueued one more
    assert queue.qsize() == 2  # 1 original + 1 refill


@pytest.mark.asyncio
async def test_actor_invalid_transition_raises():
    ctx = _make_ctx(num_nodes=1)
    queues = {DocStage.TRANSLATING: asyncio.Queue()}
    actor = DocumentActor(
        ctx=ctx,
        queue_map=queues,
        available_stages=[DocStage.TRANSLATING],
        max_inflight_per_doc=10,
        translator=None,
        group_nodes_fn=lambda nodes, **kw: [],
    )
    with pytest.raises(StateError):
        actor._transition_to(DocStage.DONE)  # invalid from PREPROCESSED
```

- [ ] **Step 2: Run tests, confirm they fail**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_scheduler.py -q
```

Expected: FAIL — `DocumentActor` not defined.

- [ ] **Step 3: Implement DocumentActor**

Add to `scheduler.py`:

```python
class DocumentActor:
    """Single-writer state machine for a document's translation lifecycle.

    Only the DocumentActor modifies DocumentContext.translated_nodes and stage.
    Workers communicate via CompletionEvent through the dispatcher.
    """

    def __init__(
        self,
        ctx: DocumentContext,
        queue_map: dict[DocStage, asyncio.Queue],
        available_stages: list[DocStage],
        max_inflight_per_doc: int,
        translator: Any,
        group_nodes_fn: Callable,
        finalize_fn: Callable | None = None,
        on_done_callback: Callable | None = None,
        progress: Any | None = None,
    ) -> None:
        self._ctx = ctx
        self._queues = queue_map
        self._available_stages = available_stages
        self._max_inflight_per_doc = max_inflight_per_doc
        self._translator = translator
        self._group_nodes_fn = group_nodes_fn
        self._finalize_fn = finalize_fn
        self.on_done_callback = on_done_callback
        self._progress = progress
        self._lock = asyncio.Lock()
        self._pending_groups: list[GroupTask] = []
        self._inflight = 0

    async def start(self, initial_groups: list[GroupTask]) -> None:
        """Transition PREPROCESSED → TRANSLATING and emit first batch."""
        self._transition_to(DocStage.TRANSLATING)
        self._pending_groups = list(initial_groups)
        self._ctx.pending_counts[DocStage.TRANSLATING.value] = len(initial_groups)
        self._inflight = 0
        self._emit_up_to_credit(DocStage.TRANSLATING)
        if self._progress is not None:
            await self._progress.add_groups(DocStage.TRANSLATING.value, len(initial_groups))

    async def on_completion(self, event: CompletionEvent) -> None:
        async with self._lock:
            # 1. Unpack group response into per-node translations.
            #    A group may contain multiple nodes packed with NODE_START/END
            #    markers. _ungroup_nodes() parses these and returns a dict of
            #    {nid: Node} with translated_text set per node. For failed
            #    groups (ok=False), each node gets empty translated_text.
            if event.ok and event.response:
                unpacked = self._translator._ungroup_nodes(
                    event.response, self._ctx.nodes,
                )
                for nid, node in unpacked.items():
                    self._ctx.translated_nodes[nid] = node
                # Backfill: any node_id in the group that was NOT unpacked
                # (incomplete payload, missing NODE_START/END marker) must be
                # recorded as failed so _collect_failed_nodes() sees it.
                for nid in event.node_ids:
                    if nid not in unpacked and nid in self._ctx.nodes:
                        orig = self._ctx.nodes[nid]
                        self._ctx.translated_nodes[nid] = Node(
                            nid=nid,
                            origin_text=orig.origin_text,
                            translated_text="",
                        )
            else:
                for nid in event.node_ids:
                    if nid in self._ctx.nodes:
                        orig = self._ctx.nodes[nid]
                        self._ctx.translated_nodes[nid] = Node(
                            nid=nid,
                            origin_text=orig.origin_text,
                            translated_text="",
                        )

            # 2. Apply placeholder typo fix + alignment on newly unpacked nodes
            valid_phs = self._ctx.store.placeholders()
            for nid in event.node_ids:
                node = self._ctx.translated_nodes.get(nid)
                if node is None or not node.translated_text:
                    continue
                if valid_phs:
                    node.translated_text = self._translator._fix_placeholder_typos(
                        node.translated_text, valid_phs,
                    )
                orig = self._ctx.nodes.get(nid)
                if orig is not None:
                    node.translated_text = self._translator._align_placeholders(
                        orig.origin_text, node.translated_text,
                    )

            # 3. Decrement pending and inflight
            stage_key = event.stage.value
            self._ctx.pending_counts[stage_key] = (
                self._ctx.pending_counts.get(stage_key, 1) - 1
            )
            self._inflight = max(self._inflight - 1, 0)

            # 4. Credit refill
            if self._pending_groups:
                self._emit_up_to_credit(event.stage)

            # 5. Report progress
            if self._progress is not None:
                await self._progress.advance_groups(stage_key, 1)

            # 6. Check if stage is complete
            if (
                self._ctx.pending_counts.get(stage_key, 0) <= 0
                and not self._pending_groups
            ):
                await self._try_advance()

    async def _try_advance(self) -> None:
        # Use the translator's real failure detection (placeholder mismatch,
        # target-script guardrails, similarity ratio, etc.) — not just
        # "empty string" checks.
        failed = self._translator._collect_failed_nodes(self._ctx.translated_nodes)
        next_stage = self._next_available_stage()
        if failed and next_stage is not None:
            self._transition_to(next_stage)
            grouped = self._group_nodes_fn(failed)
            groups = [
                GroupTask(
                    doc_id=self._ctx.doc_id,
                    stage=next_stage,
                    group_index=i,
                    node_ids=node_ids if isinstance(node_ids, tuple) else (node_ids,),
                    group_text=text,
                )
                for i, (node_ids, text) in enumerate(grouped)
            ]
            self._pending_groups = groups
            self._ctx.pending_counts[next_stage.value] = len(groups)
            self._ctx.stage_group_counts[next_stage.value] = len(groups)
            self._ctx.retry_rounds += 1
            self._inflight = 0
            self._emit_up_to_credit(next_stage)
            if self._progress is not None:
                await self._progress.add_groups(next_stage.value, len(groups))
        else:
            self._transition_to(DocStage.FINALIZING)
            await self._finalize()

    def _next_available_stage(self) -> DocStage | None:
        current_idx = STAGE_ORDER.index(self._ctx.stage) if self._ctx.stage in STAGE_ORDER else -1
        for stage in STAGE_ORDER[current_idx + 1:]:
            if stage in self._queues:
                return stage
        return None

    def _emit_up_to_credit(self, stage: DocStage) -> None:
        while self._pending_groups and self._inflight < self._max_inflight_per_doc:
            group = self._pending_groups.pop(0)
            self._queues[stage].put_nowait(group)
            self._inflight += 1

    def _transition_to(self, target: DocStage) -> None:
        if target not in TRANSITIONS.get(self._ctx.stage, []):
            raise StateError(
                f"invalid transition {self._ctx.stage} → {target}"
            )
        self._ctx.stage = target

    async def _finalize(self) -> None:
        if self._finalize_fn is not None:
            await self._finalize_fn(self._ctx)
        self._transition_to(DocStage.DONE)
        if self._progress is not None:
            await self._progress.advance_docs()
        if self.on_done_callback is not None:
            self.on_done_callback()
```

- [ ] **Step 4: Run tests, confirm they pass**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_scheduler.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/translator/scheduler.py python/deepresearch_flow/translator/tests/test_scheduler.py
git commit -m "feat(translator): add DocumentActor with state machine and emission credit"
```

---

## Task 4: Build Scheduler (Queues, Workers, Dispatcher)

**Files:**
- Modify: `python/deepresearch_flow/translator/scheduler.py`
- Modify: `python/deepresearch_flow/translator/tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests for QueueConfig and Scheduler**

Add to `test_scheduler.py`:

```python
from deepresearch_flow.translator.scheduler import (
    QueueConfig,
    Scheduler,
)
from deepresearch_flow.translator.engine import MarkdownTranslator


@pytest.mark.asyncio
async def test_scheduler_single_document_all_success():
    """One document, all nodes succeed on initial pass."""
    # This test uses a mock translator that always returns "translated"
    translator = MarkdownTranslator.__new__(MarkdownTranslator)
    # Minimal config
    from deepresearch_flow.translator.config import TranslateConfig
    translator.cfg = TranslateConfig()
    translator.protector = None  # Not used — preprocess is mocked

    main_sem = asyncio.Semaphore(4)
    global_sem = asyncio.Semaphore(4)

    config = QueueConfig(
        stage=DocStage.TRANSLATING,
        workers=2,
        provider_semaphore=main_sem,
        route_pool=None,
        provider=None,
        model=None,
        api_keys=[],
        max_tokens=None,
        retry_limit=1,
        group_max_chars=None,
    )
    retry_config = QueueConfig(
        stage=DocStage.RETRYING,
        workers=1,
        provider_semaphore=main_sem,
        route_pool=None,
        provider=None,
        model=None,
        api_keys=[],
        max_tokens=None,
        retry_limit=1,
        group_max_chars=None,
    )

    import httpx

    async with httpx.AsyncClient() as client:
        scheduler = Scheduler(
            translator=translator,
            document_window=4,
            global_semaphore=global_sem,
            queue_configs=[config, retry_config],
            progress=None,
            client=client,
            throttle=None,
            timeout=30.0,
        )
        # Verify scheduler initializes without error
        assert len(scheduler._queues) == 2
        assert DocStage.TRANSLATING in scheduler._queues
        assert DocStage.RETRYING in scheduler._queues
```

- [ ] **Step 2: Run tests, confirm they fail**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_scheduler.py::test_scheduler_single_document_all_success -q
```

Expected: FAIL — `Scheduler` class not defined.

- [ ] **Step 3: Implement QueueConfig and Scheduler skeleton**

Add to `scheduler.py`:

```python
import httpx

from deepresearch_flow.paper.config import ProviderConfig
from deepresearch_flow.paper.routing import RoutePool, RuntimeRoute
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.translator.engine import (
    KeyRotator,
    MarkdownTranslator,
    RequestThrottle,
    TranslationStats,
)
from deepresearch_flow.translator.config import TranslateConfig


_SENTINEL = object()


@dataclass
class QueueConfig:
    stage: DocStage
    workers: int
    provider_semaphore: asyncio.Semaphore
    route_pool: RoutePool | None
    provider: ProviderConfig | None
    model: str | None
    api_keys: list[str]
    max_tokens: int | None
    retry_limit: int
    group_max_chars: int | None


class Scheduler:
    def __init__(
        self,
        translator: MarkdownTranslator,
        document_window: int,
        global_semaphore: asyncio.Semaphore,
        queue_configs: list[QueueConfig],
        progress: Any | None,
        client: httpx.AsyncClient,
        throttle: RequestThrottle | None,
        timeout: float,
    ) -> None:
        self._translator = translator
        self._document_window = document_window
        self._global_sem = global_semaphore
        self._progress = progress
        self._client = client
        self._throttle = throttle
        self._timeout = timeout

        self._queues: dict[DocStage, asyncio.Queue] = {}
        self._configs: dict[DocStage, QueueConfig] = {}
        self._result_queue: asyncio.Queue[CompletionEvent] = asyncio.Queue()
        self._actors: dict[str, DocumentActor] = {}
        self._done_count = 0
        self._total_docs = 0

        for qc in queue_configs:
            self._queues[qc.stage] = asyncio.Queue()
            self._configs[qc.stage] = qc

        # Determine available stages in order
        self._available_stages = [
            s for s in STAGE_ORDER if s in self._queues
        ]

    async def run(
        self,
        paths: list[Path],
        output_map: dict[Path, Path],
        fix_level: str,
        format_enabled: bool,
        dump_callback_factory: Callable | None = None,
        request_log_enabled: bool = False,
    ) -> list[Path]:
        """Run the full scheduler lifecycle. Returns list of failed file paths."""
        self._total_docs = len(paths)
        self._done_count = 0
        failed_files: list[Path] = []

        # Start workers
        worker_tasks: list[asyncio.Task] = []
        for stage, qc in self._configs.items():
            queue = self._queues[stage]
            for _ in range(qc.workers):
                task = asyncio.create_task(
                    self._worker(queue, qc)
                )
                worker_tasks.append(task)

        # Start dispatcher
        dispatcher_task = asyncio.create_task(self._dispatcher())

        # Start feeder
        window_sem = asyncio.Semaphore(self._document_window)
        for path in paths:
            await window_sem.acquire()
            try:
                ctx = await self._preprocess(
                    path, output_map[path], fix_level, format_enabled,
                    request_log_enabled,
                )
            except Exception as exc:
                failed_files.append(path)
                logger.error("Failed to preprocess %s: %s", path, exc)
                window_sem.release()
                if self._progress is not None:
                    await self._progress.advance_docs()
                self._done_count += 1
                continue

            def make_release(sem: asyncio.Semaphore) -> Callable:
                return sem.release

            actor = DocumentActor(
                ctx=ctx,
                queue_map=self._queues,
                available_stages=self._available_stages,
                max_inflight_per_doc=self._configs[DocStage.TRANSLATING].workers * 2,
                translator=self._translator,
                group_nodes_fn=self._make_group_fn(ctx),
                finalize_fn=self._make_finalize_fn(format_enabled),
                on_done_callback=make_release(window_sem),
                progress=self._progress,
            )
            self._actors[ctx.doc_id] = actor

            # Build initial GroupTasks
            initial_groups = self._build_group_tasks(ctx, DocStage.TRANSLATING)
            await actor.start(initial_groups)

        # Wait for all documents to complete
        while self._done_count < self._total_docs:
            await asyncio.sleep(0.05)

        # Shutdown workers
        for stage, queue in self._queues.items():
            for _ in range(self._configs[stage].workers):
                await queue.put(_SENTINEL)
        await asyncio.gather(*worker_tasks)

        # Shutdown dispatcher
        dispatcher_task.cancel()
        try:
            await dispatcher_task
        except asyncio.CancelledError:
            pass

        return failed_files

    async def _preprocess(
        self,
        path: Path,
        output_path: Path,
        fix_level: str,
        format_enabled: bool,
        request_log_enabled: bool,
    ) -> DocumentContext:
        text = path.read_text(encoding="utf-8")
        request_log = [] if request_log_enabled else None
        result = await self._translator.preprocess_document(
            text, fix_level, format_enabled, request_log=request_log,
        )
        doc_id = f"{path.stem}.{id(path)}"
        return DocumentContext(
            doc_id=doc_id,
            source_path=path,
            output_path=output_path,
            original_text=result.original_text,
            protected_text=result.protected_text,
            segments=result.segments,
            nodes=result.nodes,
            store=result.store,
            total_nodes=result.total_nodes,
            skip_count=result.skip_count,
            initial_groups_count=len(result.initial_groups),
            request_log=request_log,
        )

    def _build_group_tasks(
        self, ctx: DocumentContext, stage: DocStage,
    ) -> list[GroupTask]:
        # Use existing _group_nodes to get group text strings
        # Then pair with node IDs
        groups = self._translator._group_nodes(ctx.nodes)
        tasks: list[GroupTask] = []
        # _group_nodes returns list of group text strings
        # We need to figure out which nodes are in each group
        # by unpacking with _ungroup_nodes
        for i, group_text in enumerate(groups):
            # Parse node IDs from group text markers
            node_ids = tuple(
                int(m.group(1))
                for m in self._translator._rx_node_unpack.finditer(group_text)
            )
            tasks.append(GroupTask(
                doc_id=ctx.doc_id,
                stage=stage,
                group_index=i,
                node_ids=node_ids,
                group_text=group_text,
            ))
        return tasks

    def _make_group_fn(self, ctx: DocumentContext) -> Callable:
        def group_fn(failed_nodes: dict[int, Node], **kwargs: Any) -> list[tuple[tuple[int, ...], str]]:
            retry_max = self._translator.cfg.retry_group_max_chars or max(
                1024, self._translator.cfg.max_chunk_chars // 2
            )
            groups = self._translator._group_nodes(
                failed_nodes,
                only_ids=sorted(failed_nodes.keys()),
                max_chunk_chars=retry_max,
                include_translated=True,
            )
            result = []
            for group_text in groups:
                node_ids = tuple(
                    int(m.group(1))
                    for m in self._translator._rx_node_unpack.finditer(group_text)
                )
                result.append((node_ids, group_text))
            return result
        return group_fn

    def _make_finalize_fn(self, format_enabled: bool) -> Callable:
        async def finalize(ctx: DocumentContext) -> None:
            # Placeholder fix + alignment already applied in on_completion.
            # Merge translated_nodes back into nodes for reassembly.
            for nid, node in ctx.translated_nodes.items():
                if nid in ctx.nodes:
                    ctx.nodes[nid].translated_text = node.translated_text

            result = await self._translator.finalize_document(
                ctx.original_text,
                ctx.protected_text,
                ctx.segments,
                ctx.nodes,
                ctx.store,
                format_enabled,
            )
            ctx.output_path.parent.mkdir(parents=True, exist_ok=True)
            ctx.output_path.write_text(result, encoding="utf-8")

            # Log per-document stats matching current CLI output contract
            failed = self._translator._collect_failed_nodes(ctx.translated_nodes)
            failed_count = len(failed)
            success_count = max(ctx.total_nodes - failed_count, 0)
            retry_groups = sum(
                v for k, v in ctx.stage_group_counts.items()
                if k != DocStage.TRANSLATING.value
            )
            logger.info(
                "Translated %s | nodes=%d ok=%d fail=%d skip=%d groups=%d retries=%d",
                ctx.source_path.name,
                ctx.total_nodes,
                success_count,
                failed_count,
                ctx.skip_count,
                ctx.initial_groups_count,
                retry_groups,
            )
        return finalize

    async def _worker(self, queue: asyncio.Queue, config: QueueConfig) -> None:
        rotator = KeyRotator(config.api_keys) if config.api_keys else None
        while True:
            task = await queue.get()
            if task is _SENTINEL:
                queue.task_done()
                break
            try:
                # 1. Acquire route (may wait on cooldown — no permits held)
                route: RuntimeRoute | None = None
                api_key: str | None = None
                if config.route_pool is not None:
                    route = await config.route_pool.get()
                elif rotator is not None:
                    api_key = await rotator.next_key()

                # 2. Acquire permits, then call LLM.
                #    Throttle ownership stays inside _translate_group()
                #    (it already calls throttle.tick() before each attempt).
                #    Do NOT tick here — that would double-throttle.
                async with config.provider_semaphore:
                    async with self._global_sem:
                        # Thread request_log from DocumentContext for debug output
                        ctx = self._actors.get(task.doc_id)
                        req_log = ctx._ctx.request_log if ctx else None
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
                            # dump_callback: scheduler v1 does NOT wire
                            # per-attempt dump_callback into workers. The
                            # --dump-nodes / --dump-protected / --dump-placeholders
                            # debug outputs are written once at finalize time
                            # (via the compat wrapper or a future scheduler hook),
                            # not per-group. request_log IS preserved above.
                            # This is an intentional scope cut — per-attempt
                            # staged dumps can be added later without changing
                            # the scheduler's core interfaces.
                            None,
                            route=route,
                        )
                await self._result_queue.put(CompletionEvent(
                    doc_id=task.doc_id,
                    stage=task.stage,
                    group_index=task.group_index,
                    node_ids=task.node_ids,
                    ok=True,
                    response=response,
                ))
            except ProviderError as exc:
                if route is not None and config.route_pool is not None:
                    quota_hit = await config.route_pool.mark_quota_exceeded(
                        route, str(exc), exc.status_code,
                    )
                    if not quota_hit and exc.retryable:
                        await config.route_pool.mark_error(route)
                await self._result_queue.put(CompletionEvent(
                    doc_id=task.doc_id,
                    stage=task.stage,
                    group_index=task.group_index,
                    node_ids=task.node_ids,
                    ok=False,
                    response="",
                ))
            except Exception as exc:
                logger.error(
                    "Unexpected error in worker (doc=%s stage=%s group=%d): %s",
                    task.doc_id, task.stage.value, task.group_index, exc,
                )
                await self._result_queue.put(CompletionEvent(
                    doc_id=task.doc_id,
                    stage=task.stage,
                    group_index=task.group_index,
                    node_ids=task.node_ids,
                    ok=False,
                    response="",
                ))
            finally:
                queue.task_done()

    async def _dispatcher(self) -> None:
        while True:
            event = await self._result_queue.get()
            actor = self._actors.get(event.doc_id)
            if actor is None:
                logger.warning("No actor for doc_id=%s", event.doc_id)
                continue
            await actor.on_completion(event)
            if actor._ctx.stage == DocStage.DONE:
                self._done_count += 1
```

- [ ] **Step 4: Run tests, confirm they pass**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_scheduler.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/translator/scheduler.py python/deepresearch_flow/translator/tests/test_scheduler.py
git commit -m "feat(translator): add Scheduler with queues, workers, and dispatcher"
```

---

## Task 5: Build ProgressReporter

**Files:**
- Create: `python/deepresearch_flow/translator/progress.py`
- Create: `python/deepresearch_flow/translator/tests/test_progress.py`

- [ ] **Step 1: Write failing tests for ProgressReporter**

Create `python/deepresearch_flow/translator/tests/test_progress.py`:

```python
"""Black-box tests for ProgressReporter."""

import pytest
from deepresearch_flow.translator.progress import ProgressReporter


@pytest.mark.asyncio
async def test_reporter_creation_with_stages():
    reporter = ProgressReporter(doc_total=10, stages=["initial", "retry"])
    assert reporter.doc_bar.total == 10
    assert "initial" in reporter.stage_bars
    assert "retry" in reporter.stage_bars
    await reporter.close()


@pytest.mark.asyncio
async def test_reporter_add_and_advance_groups():
    reporter = ProgressReporter(doc_total=5, stages=["initial"])
    await reporter.add_groups("initial", 20)
    assert reporter.stage_bars["initial"].total == 20
    await reporter.advance_groups("initial", 5)
    assert reporter.stage_bars["initial"].n == 5
    await reporter.close()


@pytest.mark.asyncio
async def test_reporter_advance_docs():
    reporter = ProgressReporter(doc_total=3, stages=[])
    await reporter.advance_docs()
    assert reporter.doc_bar.n == 1
    await reporter.close()


@pytest.mark.asyncio
async def test_reporter_unknown_stage_is_ignored():
    reporter = ProgressReporter(doc_total=1, stages=["initial"])
    # Should not raise
    await reporter.add_groups("nonexistent", 5)
    await reporter.advance_groups("nonexistent", 1)
    await reporter.close()
```

- [ ] **Step 2: Run tests, confirm they fail**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_progress.py -q
```

Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement ProgressReporter**

Create `python/deepresearch_flow/translator/progress.py`:

```python
"""Per-stage progress reporting for the translator scheduler."""

from __future__ import annotations

import asyncio

from tqdm import tqdm


class ProgressReporter:
    """Multi-bar progress display with per-stage tracking.

    Displays:
        documents:  12/48  [████████░░░░░░░░]
        initial:   340/500 [█████████████░░░]
        retry:       8/12  [██████████░░░░░░]
        fallback:    0/0
    """

    def __init__(self, doc_total: int, stages: list[str]) -> None:
        self.doc_bar = tqdm(
            total=doc_total, desc="documents", unit="doc", position=0,
        )
        self.stage_bars: dict[str, tqdm] = {}
        for i, stage in enumerate(stages):
            self.stage_bars[stage] = tqdm(
                total=0, desc=stage, unit="group",
                position=i + 1, leave=False,
            )
        self._lock = asyncio.Lock()

    async def add_groups(self, stage: str, count: int) -> None:
        if count <= 0 or stage not in self.stage_bars:
            return
        async with self._lock:
            bar = self.stage_bars[stage]
            bar.total = (bar.total or 0) + count
            bar.refresh()

    async def advance_groups(self, stage: str, count: int) -> None:
        if count <= 0 or stage not in self.stage_bars:
            return
        async with self._lock:
            self.stage_bars[stage].update(count)

    async def advance_docs(self, count: int = 1) -> None:
        if count <= 0:
            return
        async with self._lock:
            self.doc_bar.update(count)

    async def close(self) -> None:
        async with self._lock:
            for bar in self.stage_bars.values():
                bar.close()
            self.doc_bar.close()
```

- [ ] **Step 4: Run tests, confirm they pass**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_progress.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/translator/progress.py python/deepresearch_flow/translator/tests/test_progress.py
git commit -m "feat(translator): add ProgressReporter with per-stage bars"
```

---

## Task 6: Rewire CLI

**Files:**
- Modify: `python/deepresearch_flow/translator/cli.py`

- [ ] **Step 1: Add new CLI parameters**

Add the following options to the `translate` command in `cli.py` (after the existing `--group-concurrency` option at line 141):

```python
@click.option(
    "--document-window",
    "document_window",
    default=None,
    type=int,
    help="Max documents simultaneously in-flight (default: all)",
)
@click.option(
    "--initial-workers",
    "initial_workers",
    default=None,
    type=int,
    help="Worker count for initial translation queue",
)
@click.option(
    "--retry-workers",
    "retry_workers",
    default=None,
    type=int,
    help="Worker count for retry queue",
)
@click.option(
    "--fallback-workers",
    "fallback_workers",
    default=2,
    show_default=True,
    type=int,
    help="Worker count for fallback queue",
)
@click.option(
    "--fallback-2-workers",
    "fallback_2_workers",
    default=2,
    show_default=True,
    type=int,
    help="Worker count for fallback_2 queue",
)
@click.option(
    "--main-concurrency",
    "main_concurrency",
    default=None,
    type=int,
    help="Provider-level concurrency for main model",
)
@click.option(
    "--fallback-concurrency",
    "fallback_concurrency_val",
    default=None,
    type=int,
    help="Provider-level concurrency for fallback model",
)
@click.option(
    "--fallback-2-concurrency",
    "fallback_2_concurrency_val",
    default=None,
    type=int,
    help="Provider-level concurrency for fallback_2 model",
)
```

Mark `--group-concurrency` as deprecated:

```python
@click.option(
    "--group-concurrency",
    "group_concurrency",
    default=None,
    type=int,
    help="[DEPRECATED: use --initial-workers] Concurrent translation groups per document",
)
```

- [ ] **Step 2: Add deprecation handling and parameter resolution**

Add parameter resolution logic at the start of the `translate()` function body, after `configure_logging(verbose)`:

```python
    # Resolve deprecated --group-concurrency
    if group_concurrency is not None:
        if initial_workers is None:
            click.echo(
                "Warning: --group-concurrency is deprecated, use --initial-workers",
                err=True,
            )
            initial_workers = group_concurrency
        else:
            click.echo(
                "Warning: --group-concurrency ignored because --initial-workers is set",
                err=True,
            )

    # Defaults
    if initial_workers is None:
        initial_workers = 1
    if retry_workers is None:
        retry_workers = max(initial_workers // 4, 1)
    # document_window default is set later, after to_process is computed
    # if document_window is None:
    #     document_window = len(to_process)
```

- [ ] **Step 3: Build QueueConfigs and Scheduler**

Replace the existing `process_one` / `run` section (lines 452–569) with scheduler construction and execution:

```python
    # Build semaphores
    global_sem = asyncio.Semaphore(max_concurrency)
    main_sem = asyncio.Semaphore(main_concurrency) if main_concurrency else asyncio.Semaphore(max_concurrency)
    fb_sem = asyncio.Semaphore(fallback_concurrency_val) if fallback_concurrency_val else asyncio.Semaphore(max_concurrency)
    fb2_sem = asyncio.Semaphore(fallback_2_concurrency_val) if fallback_2_concurrency_val else asyncio.Semaphore(max_concurrency)

    queue_configs: list[QueueConfig] = [
        QueueConfig(
            stage=DocStage.TRANSLATING,
            workers=initial_workers,
            provider_semaphore=main_sem,
            route_pool=route_pool,
            provider=provider,
            model=model_name,
            api_keys=provider.api_keys,
            max_tokens=max_tokens,
            retry_limit=max(retry_times, 1),
            group_max_chars=None,
        ),
        QueueConfig(
            stage=DocStage.RETRYING,
            workers=retry_workers,
            provider_semaphore=main_sem,  # shared with initial
            route_pool=route_pool,
            provider=provider,
            model=model_name,
            api_keys=provider.api_keys,
            max_tokens=max_tokens,
            retry_limit=max(retry_times, 1),
            group_max_chars=None,
        ),
    ]

    if fallback_provider and fallback_model_name:
        queue_configs.append(QueueConfig(
            stage=DocStage.FALLBACK_1,
            workers=fallback_workers,
            provider_semaphore=fb_sem,
            route_pool=fallback_route_pool,
            provider=fallback_provider,
            model=fallback_model_name,
            api_keys=fallback_provider.api_keys,
            max_tokens=fallback_max_tokens,
            retry_limit=fallback_retry_times or max(retry_times, 1),
            group_max_chars=None,
        ))

    if fallback_provider_2 and fallback_model_name_2:
        queue_configs.append(QueueConfig(
            stage=DocStage.FALLBACK_2,
            workers=fallback_2_workers,
            provider_semaphore=fb2_sem,
            route_pool=fallback_route_pool_2,
            provider=fallback_provider_2,
            model=fallback_model_name_2,
            api_keys=fallback_provider_2.api_keys,
            max_tokens=fallback_max_tokens_2,
            retry_limit=fallback_retry_times_2 or max(retry_times, 1),
            group_max_chars=None,
        ))

    if document_window is None:
        document_window = len(to_process)

    from deepresearch_flow.translator.scheduler import Scheduler, DocStage, QueueConfig
    from deepresearch_flow.translator.progress import ProgressReporter

    stage_names = [qc.stage.value for qc in queue_configs]

    async def run() -> None:
        progress = ProgressReporter(len(to_process), stage_names)
        try:
            async with httpx.AsyncClient() as client:
                scheduler = Scheduler(
                    translator=translator,
                    document_window=document_window,
                    global_semaphore=global_sem,
                    queue_configs=queue_configs,
                    progress=progress,
                    client=client,
                    throttle=throttle,
                    timeout=timeout,
                )
                nonlocal failed_files
                failed_files = await scheduler.run(
                    paths=to_process,
                    output_map=output_map,
                    fix_level=fix_level,
                    format_enabled=not no_format,
                    request_log_enabled=dump_requests_log,
                )
        finally:
            await progress.close()

    asyncio.run(run())
```

- [ ] **Step 4: Update the function signature**

Add the new parameters to the `translate()` function signature. Keep all existing parameters. Add the new ones after the existing `group_concurrency`:

```python
def translate(
    ...
    group_concurrency: int | None,
    document_window: int | None,
    initial_workers: int | None,
    retry_workers: int | None,
    fallback_workers: int,
    fallback_2_workers: int,
    main_concurrency: int | None,
    fallback_concurrency_val: int | None,
    fallback_2_concurrency_val: int | None,
    ...
) -> None:
```

- [ ] **Step 5: Run existing CLI tests**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_cli_translate.py -q
```

Expected: PASS (existing tests should still work — they don't pass the new params, so defaults apply).

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/translator/cli.py
git commit -m "feat(translator): wire scheduler into CLI with new concurrency params"
```

---

## Task 7: Run Cross-Module Regression

**Files:**
- No new code unless a regression forces a minimal follow-up change

- [ ] **Step 1: Run the full translator test suite**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/ -q
```

Expected: PASS

- [ ] **Step 2: Run the scheduler and progress tests**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_scheduler.py python/deepresearch_flow/translator/tests/test_progress.py -v
```

Expected: PASS

- [ ] **Step 3: Run the existing engine guardrail tests**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_engine_translate_guardrails.py -q
```

Expected: PASS — the `translate()` compat wrapper should produce identical results.

- [ ] **Step 4: Commit any test-only adjustments**

Only if there are test fixes needed:

```bash
git add python/deepresearch_flow/translator/tests/
git commit -m "test: fix regressions from scheduler integration"
```

---

## Acceptance Criteria

- Documents are processed concurrently, bounded by `--document-window`.
- Initial/retry/fallback/fallback_2 each have their own queue and worker pool.
- Initial and retry share the same provider semaphore; fallback and fallback_2 have independent semaphores.
- The state machine enforces forward-only transitions; invalid transitions raise `StateError`.
- Per-document emission credit prevents queue flooding by large documents.
- Route acquisition happens before semaphore acquisition (no permit waste during cooldown).
- `--group-concurrency` is deprecated with warning, mapped to `--initial-workers`.
- Failed nodes fall back to origin text (partial output behavior unchanged).
- Progress display shows per-stage bars (documents / initial / retry / fallback).
- Existing tests pass without modification (compat wrapper).
- `translate()` remains as a convenience wrapper for single-document callers.

---

## Execution Notes

- Task 1 must complete before Tasks 2–5 (they depend on the extracted methods).
- Tasks 2 and 5 can run in parallel (no dependency between state machine and progress).
- Task 3 depends on Task 2 (DocumentActor depends on state machine).
- Task 4 depends on Tasks 2 and 3 (Scheduler depends on DocumentActor).
- Task 6 depends on Tasks 1, 4, and 5 (CLI wires everything together).
- Task 7 runs last as final validation.
