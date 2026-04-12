# Translator Concurrency Architecture Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan for this spec.

**Goal:** Replace the serial document-processing loop in the translator CLI with a multi-document, multi-queue concurrent scheduler that separates initial translation from retry/fallback stages, without changing translation correctness or output behavior.

**Non-goals:**

- Translation quality or prompt engineering
- Provider-specific rate-limit abstractions (RoutePool is retained as-is)
- Changing partial-output behavior (failed nodes still fall back to origin text)
- Terminology, glossary, or content-policy changes

---

## Problem Statement

The translator CLI processes documents serially:

```python
for path in to_process:
    await process_one(path, client, progress)
```

Each document runs its full lifecycle — preprocess, initial translation, retry rounds, fallback rounds, finalize — before the next document begins. This means:

1. **Document-level serialization** is the primary throughput bottleneck. With 48 documents, total wall-clock time is the sum of all individual document times.
2. **Retry tail-latency blocks everything.** A small number of failed groups that enter retry/fallback stages hold up the entire batch, even though most documents complete on the first pass.
3. **Provider budgets are underutilized.** When fallback uses a different provider (commercial API vs. local GPU), the fallback provider sits idle during initial translation and vice versa.
4. **Progress is opaque.** Initial and retry groups share one counter; users cannot tell whether the system is doing productive first-pass work or stuck retrying failures.

Additionally, `engine.py` contains ~220 lines of near-identical retry/fallback/fallback_2 loop code that should be unified.

---

## Architecture Overview

```
CLI
  └── Scheduler
       ├── feeder (document preprocessing window)
       │    └── preprocess_document() → DocumentContext
       │
       ├── initial_queue ──→ worker pool (main provider)
       ├── retry_queue   ──→ worker pool (main provider, shared semaphore)
       ├── fallback_queue ──→ worker pool (fallback provider)
       ├── fallback_2_queue ──→ worker pool (fallback_2 provider)
       │
       ├── result_queue ──→ dispatcher
       │    └── routes CompletionEvent to DocumentActor
       │
       ├── DocumentActor (per document, single-writer state machine)
       │    └── on_completion → apply results → advance state → emit next-stage tasks or finalize
       │
       └── ProgressReporter
            └── documents / initial / retry / fallback / fallback_2
```

### Key Principles

- **4 queues, one per stage.** initial and retry share the same provider (RoutePool); fallback and fallback_2 each have their own provider. Queues that have no configured provider are not created.
- **DocumentActor as single writer.** Workers never mutate DocumentContext. They produce CompletionEvent messages; the DocumentActor applies results, decides the next state transition, and emits tasks to the appropriate queue.
- **State machine drives document lifecycle.** Each document progresses through a strict state machine with no backward transitions.
- **Two-layer concurrency control.** A global semaphore caps total in-flight LLM requests. Per-provider semaphores cap requests to each provider independently. initial and retry share one provider semaphore.

---

## State Machine

```
PREPROCESSED
    │ emit initial groups
    ▼
TRANSLATING
    │ all initial groups completed
    ▼
 has failures? ──no──→ FINALIZING
    │ yes                    │
    ▼                        │
RETRYING                     │
    │ all retry groups done  │
    ▼                        │
 has failures? ──no──────────┤
    │ yes                    │
    ▼                        │
FALLBACK_1                   │
    │ all fallback done      │
    ▼                        │
 has failures? ──no──────────┤
    │ yes                    │
    ▼                        │
FALLBACK_2                   │
    │ all fallback_2 done    │
    ▼                        │
FINALIZING ◄─────────────────┘
    │ reassemble / restore / write
    ▼
DONE
```

### State Definition

```python
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
    DocStage.TRANSLATING:  [DocStage.RETRYING, DocStage.FINALIZING],
    DocStage.RETRYING:     [DocStage.FALLBACK_1, DocStage.FINALIZING],
    DocStage.FALLBACK_1:   [DocStage.FALLBACK_2, DocStage.FINALIZING],
    DocStage.FALLBACK_2:   [DocStage.FINALIZING],
    DocStage.FINALIZING:   [DocStage.DONE],
}
```

### Transition Logic

When all pending groups for the current stage reach zero, the DocumentActor calls `_try_advance()`:

1. Collect failed nodes via `_collect_failed_nodes()`.
2. Look up the next queue in stage order that exists (was configured by the user).
3. If failed nodes exist and a next queue exists: transition to that stage, generate groups from failed nodes, enqueue them, set `pending_counts[stage]`.
4. Otherwise: transition to FINALIZING and run `finalize_document()`.

Invalid transitions raise `StateError`. Stages only move forward.

---

## Data Model

### DocumentContext

```python
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

    # Translation state (written only by DocumentActor)
    translated_nodes: dict[int, Node]
    pending_counts: dict[str, int]
    stage: DocStage

    # Debug / stats
    request_log: list[dict[str, Any]] | None
    stats: TranslationStats
```

### GroupTask

```python
@dataclass(frozen=True)
class GroupTask:
    doc_id: str
    stage: DocStage
    group_index: int
    node_ids: tuple[int, ...]
    group_text: str
```

### CompletionEvent

```python
@dataclass(frozen=True)
class CompletionEvent:
    doc_id: str
    stage: DocStage
    group_index: int
    node_ids: tuple[int, ...]
    ok: bool
    response: str
```

---

## Scheduler

### Initialization

```python
class Scheduler:
    def __init__(
        self,
        translator: MarkdownTranslator,
        document_window: int,
        global_semaphore: asyncio.Semaphore,
        queue_configs: list[QueueConfig],
        progress: ProgressReporter,
        client: httpx.AsyncClient,
        throttle: RequestThrottle | None,
        timeout: float,
    ): ...
```

### QueueConfig

```python
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
```

initial and retry `QueueConfig` entries point to the **same** `provider_semaphore` instance (they share the main provider's concurrency budget).

### Queue Creation

Only queues with a configured provider are created:

- **Always:** initial_queue, retry_queue (both use main provider).
- **If `--fallback-model` is set:** fallback_queue.
- **If `--fallback-model-2` is set:** fallback_2_queue.

### Feeder

The feeder controls how many documents are simultaneously in-flight:

```python
async def feeder(self, paths: list[Path]):
    window_sem = asyncio.Semaphore(self.document_window)
    for path in paths:
        await window_sem.acquire()
        ctx = await self.preprocess_document(path)
        actor = DocumentActor(ctx, self.queue_map)
        self.actors[ctx.doc_id] = actor
        actor.on_done_callback = window_sem.release
        await actor.start()  # enqueue initial groups
```

### Worker

All queues share the same worker template:

```python
async def worker(queue, config, global_sem, translator, client, ...):
    while True:
        task = await queue.get()
        if task is SENTINEL:
            queue.task_done()
            break
        try:
            async with config.provider_semaphore:
                async with global_sem:
                    response = await translator._translate_group(
                        task.group_text, config.provider, config.model,
                        client, api_key, timeout, ...,
                        route_pool=config.route_pool,
                    )
            await result_queue.put(CompletionEvent(
                doc_id=task.doc_id, stage=task.stage,
                group_index=task.group_index, node_ids=task.node_ids,
                ok=True, response=response,
            ))
        except ProviderError:
            await result_queue.put(CompletionEvent(..., ok=False, response=""))
        finally:
            queue.task_done()
```

### Dispatcher

Routes completion events to the correct DocumentActor:

```python
async def dispatcher(self):
    while not self.all_done():
        event = await self.result_queue.get()
        actor = self.actors[event.doc_id]
        await actor.on_completion(event)
```

### Shutdown

When all documents reach DONE, the scheduler sends SENTINEL to each worker queue, awaits worker tasks, and returns.

---

## DocumentActor

```python
class DocumentActor:
    def __init__(self, ctx: DocumentContext, queue_map: dict[DocStage, asyncio.Queue]):
        self._ctx = ctx
        self._queues = queue_map
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Transition PREPROCESSED → TRANSLATING, enqueue initial groups."""
        ...

    async def on_completion(self, event: CompletionEvent) -> None:
        async with self._lock:
            # 1. Apply translated nodes (placeholder typo fix + alignment)
            # 2. Decrement pending_counts[event.stage]
            # 3. If pending reaches zero: _try_advance()

    async def _try_advance(self) -> None:
        failed = self._collect_failed_nodes()
        next_stage = self._next_available_stage()
        if failed and next_stage is not None:
            self._transition_to(next_stage)
            groups = self._group_failed_nodes(failed)
            for i, group in enumerate(groups):
                self._queues[next_stage].put_nowait(group)
            self._ctx.pending_counts[next_stage.value] = len(groups)
        else:
            self._transition_to(DocStage.FINALIZING)
            await self._finalize()

    def _transition_to(self, target: DocStage) -> None:
        if target not in TRANSITIONS[self._ctx.stage]:
            raise StateError(f"invalid transition {self._ctx.stage} → {target}")
        self._ctx.stage = target

    async def _finalize(self) -> None:
        # Delegates to translator.finalize_document()
        # Writes output file
        # Transitions to DONE
        # Calls on_done_callback (releases document window semaphore)
        ...
```

---

## Concurrency Control

### Two-Layer Semaphore Hierarchy

```
global_semaphore (--max-concurrency)
  ├── main_semaphore (--main-concurrency)       ← shared by initial + retry workers
  ├── fallback_semaphore (--fallback-concurrency)
  └── fallback_2_semaphore (--fallback-2-concurrency)
```

Workers acquire **both** the provider semaphore and global semaphore before making an LLM call. This ensures:

- Per-provider limits are respected (e.g., local GPU can handle 16 concurrent, commercial API limited to 4).
- Total system load never exceeds the global cap regardless of how many providers are active.

### Document Window

`--document-window` controls how many documents are simultaneously preprocessed and in-flight. This bounds memory usage (each document holds nodes, segments, PlaceHolderStore in memory until finalized).

---

## Progress Display

Replace the current 2-bar ProgressTracker with a multi-bar ProgressReporter:

```
documents:  12/48  [████████░░░░░░░░]
initial:   340/500 [█████████████░░░]
retry:       8/12  [██████████░░░░░░]
fallback:    0/0
```

```python
class ProgressReporter:
    def __init__(self, doc_total: int, stages: list[str]):
        self.doc_bar = tqdm(total=doc_total, desc="documents", position=0)
        self.stage_bars = {
            stage: tqdm(total=0, desc=stage, position=i+1, leave=False)
            for i, stage in enumerate(stages)
        }
        self.lock = asyncio.Lock()

    async def add_groups(self, stage: str, count: int) -> None: ...
    async def advance_groups(self, stage: str, count: int) -> None: ...
    async def advance_docs(self) -> None: ...
```

Each DocumentActor reports to the ProgressReporter when groups are enqueued (add) and when completion events are processed (advance). Document bar advances when a document reaches DONE.

---

## engine.py Refactoring

### Split translate() into Three Parts

**`preprocess_document()`** — everything before group translation:

- `fix_markdown()`
- `_format_markdown("pre")`
- `protector.protect()`
- `split_to_segments()`
- Skip placeholder-only nodes
- `_group_nodes()` → initial groups

Returns: `(protected_text, segments, nodes, store, initial_groups)`

**`finalize_document()`** — everything after all translation stages complete:

- Failed nodes fall back to `origin_text`
- `reassemble_segments()`
- `_format_markdown("post")` + `preserve_heading_levels()`
- `_normalize_markdown_blocks()`
- `_restore_protected_text()`

Returns: final restored text

**`_translate_group()`** — mostly unchanged. The `semaphore` parameter is removed from its signature because concurrency control now lives in the worker (two-layer semaphore acquisition happens before calling `_translate_group`). All other parameters remain.

### Eliminate Retry/Fallback Code Duplication

The current ~220 lines of near-identical retry → fallback → fallback_2 loops in `translate()` are replaced by the state machine and 4-queue architecture. Each queue's worker calls `_translate_group()` with different `QueueConfig` parameters. No loop duplication remains.

### Retained Helper Methods

These stay on `MarkdownTranslator` and are called by DocumentActor:

- `_group_nodes()` — used to generate groups for each stage
- `_ungroup_groups()` / `_ungroup_nodes()` — used by `on_completion`
- `_collect_failed_nodes()` — used by `_try_advance`
- `_fix_placeholder_typos()` / `_align_placeholders()` — used by `on_completion`
- `_is_placeholder_only()` — used by `preprocess_document`

### Backward Compatibility

`translate()` is retained as a convenience wrapper that internally creates a single-document Scheduler and runs it. This preserves existing test contracts and any direct callers.

---

## CLI Changes

### Removed Parameters

| Parameter | Reason |
|-----------|--------|
| `--group-concurrency` | Replaced by `--initial-workers` |

### New Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `--document-window` | no limit | Max documents simultaneously in-flight |
| `--initial-workers` | 1 | Worker count for initial queue |
| `--retry-workers` | `max(initial_workers // 4, 1)` | Worker count for retry queue |
| `--fallback-workers` | 2 | Worker count for fallback queue |
| `--fallback-2-workers` | 2 | Worker count for fallback_2 queue |
| `--main-concurrency` | no limit (= max-concurrency) | Provider-level concurrency for main model |
| `--fallback-concurrency` | no limit (= max-concurrency) | Provider-level concurrency for fallback |
| `--fallback-2-concurrency` | no limit (= max-concurrency) | Provider-level concurrency for fallback_2 |

"no limit" means no independent provider semaphore is created; only the global semaphore applies.

### Unchanged Parameters

`--max-concurrency`, `--retry-times`, `--fallback-model`, `--fallback-model-2`, `--fallback-retry-times`, `--fallback-retry-times-2`, `--timeout`, all debug and format options.

---

## Migration Stages

| Stage | Content | Independently Deliverable | Rollback |
|-------|---------|--------------------------|----------|
| 1 | Split `translate()` into `preprocess_document()` + `finalize_document()`, `translate()` calls both internally | Yes | Merge back |
| 2 | Eliminate retry/fallback/fallback_2 triple loop into unified provider-chain loop inside `translate()` | Yes | git revert |
| 3 | New `scheduler.py`: DocumentContext, DocStage state machine, DocumentActor, 4 queues + workers, Scheduler.run() | Yes | Fall back to translate() |
| 4 | Rewire `cli.py`: remove `--group-concurrency`, add new params, entry point becomes `scheduler.run()` | Yes | Revert CLI |
| 5 | ProgressReporter with per-stage bars | Yes | Revert to old ProgressTracker |

Stages 1-2 are pure refactoring with no behavior change. Stage 3 is the core new code. Stages 4-5 are wiring.

---

## Scope

### Files Modified

| File | Action |
|------|--------|
| `python/deepresearch_flow/translator/engine.py` | Refactor: split translate(), remove triple loop, retain helpers |
| `python/deepresearch_flow/translator/cli.py` | Modify: new params, replace process loop with scheduler |
| `python/deepresearch_flow/translator/scheduler.py` | Create: Scheduler, DocumentContext, DocumentActor, DocStage, workers |
| `python/deepresearch_flow/translator/progress.py` | Create: ProgressReporter |
| `python/deepresearch_flow/translator/tests/` | Modify/Create: scheduler tests, state machine tests |

### Files Not Modified

| File | Reason |
|------|--------|
| `protector.py` | No interface change |
| `placeholder.py` | No interface change |
| `segment.py` | No interface change |
| `fixers.py` | No interface change |
| `config.py` | No interface change (TranslateConfig unchanged) |
| `prompts.py` | No interface change |
| `paper/routing.py` | RoutePool retained as-is |
| `paper/llm.py` | No change |
| `paper/providers/*` | No change |

---

## Behavioral Invariants

These must hold before and after the migration:

1. **Partial output preserved.** Failed nodes fall back to origin text; the document is written normally. No quarantine mode.
2. **Placeholder integrity.** protect → translate → restore roundtrip produces the same result as current single-document flow.
3. **Provider isolation.** Each queue uses exactly one provider/RoutePool. No cross-queue provider sharing.
4. **Stage ordering.** A document's retry groups are only emitted after all its initial groups complete. Fallback groups only after all retries complete. No interleaving within a document.
5. **Deterministic output.** Given identical inputs and provider responses, the output text is identical regardless of document-window or worker counts.
