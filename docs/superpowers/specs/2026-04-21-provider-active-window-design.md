# Provider / URL Active Window Design Spec

**Date:** 2026-04-21
**Status:** Draft
**Scope:** Add 24-hour per-URL active time windows to main providers, embedding providers, and rerank providers. URLs outside their active window are skipped at route-selection time; when every URL in a pool is outside its window the pool raises `ProviderOutOfActiveWindow` without sleeping.

## Motivation

Today every `base.url` in `config.toml` is treated as 24/7 available. Real deployments have URLs that are only reachable in specific hours — for example:

- A local Ollama endpoint on a shared GPU that is only idle at night.
- A VPN-tunneled endpoint that is only open during working hours.
- A relay that is rate-limited to specific windows to stay inside a free-tier quota.

There is no way to express "only use this URL between 22:00 and 06:00". Operators work around it by commenting URLs in/out manually, which is error-prone and defeats the weighted routing pool.

## Goals

- Let each `base.url` (main / embedding / rerank) declare one or more 24-hour active windows.
- When a URL is outside its window, treat it as unavailable for routing — same as quota-exhausted, just with a different reason.
- Default: no window = available 24/7 (backward-compatible).
- Uniform semantics across main providers, `embedding.providers`, and `rerank.providers`.
- Support cross-midnight windows (`"22:00-06:00"`) and multiple disjoint windows per URL.

## Non-Goals

- Provider-level default windows. Only URL-level. Duplicating the same string across a provider's URLs is acceptable and explicit.
- Automatic "wait until the next window opens and retry". If every URL in a pool is out-of-window the pool raises; the caller decides whether to retry later. Blocking an embedding batch until 08:00 tomorrow is not useful.
- Automatic fallback to a different provider. Embedding/rerank stay single-provider at runtime; routing within one provider already handles multi-base pools.
- Per-key windows. Windows live on the URL, not the API key.
- Calendar-style constraints (days of week, specific dates). Only time-of-day. Cron-like support is out of scope.

## Configuration

### New fields on `base` entries

Each item of `providers[].base`, `embedding.providers[].base`, and `rerank.providers[].base` gains two optional fields:

- `active_windows`: `list[str]`. Each entry `"HH:MM-HH:MM"`, 24-hour.
  - Interval is left-closed, right-open: `[start, end)`.
  - `end > start` means "same day".
  - `end < start` means "cross midnight", e.g. `"22:00-06:00"` = `[22:00, 24:00) ∪ [00:00, 06:00)`.
  - `start == end` is rejected (ambiguous: empty or all-day?).
  - **Start boundary**: hours `0–23`, minutes `0–59`. `"24:00"` as a start is rejected.
  - **End boundary**: hours `0–23` with minutes `0–59`, OR the literal `"24:00"` (meaning end-of-day). `"24:00"` on the end side is normalized to the sentinel `time(0, 0)` carrying an "is end-of-day" flag internally, so that `"23:00-24:00"` and `"00:00-24:00"` are expressible without becoming cross-midnight.
- `active_timezone`: `str | None`. IANA zone name (e.g. `"Asia/Shanghai"`). Default: **system local time zone** (resolved once at `RoutePool` construction via `datetime.now().astimezone().tzinfo`, never `UTC` and never `now.tzinfo` of whatever is passed in).

Omitted or empty `active_windows` means "always active" (24/7).

### Example

```toml
[[providers]]
name = "ollama"
type = "ollama"

base = [
  {
    url = "http://localhost:11434",
    weight = 1,
    active_windows = ["22:00-06:00"],
    active_timezone = "Asia/Shanghai",
    key = [{ value = "local", weight = 1 }],
  },
]

models = [
  { model_name = "llama3.1", is_stream = true, is_support_json_schema = false, is_support_json_object = true },
]

[[embedding.providers]]
name = "ollama"
type = "openai_compatible"
base = [
  { url = "http://localhost:11434/v1", weight = 1,
    active_windows = ["09:00-12:00", "14:00-18:00"],
    key = [{ value = "ollama", weight = 1 }] },
]
models = [
  { model_name = "Qwen3-Embedding-4B", dimensions = 1024, max_context = 32768 },
]
```

### Validation (at config-load time)

- `active_windows` entries are parsed once during config load; format errors surface at startup, not at first request.
- `active_timezone` is validated through `zoneinfo.ZoneInfo(name)`; unknown zones fail loading.

## Runtime Semantics

### Selection gate

A `BaseConfig` is **active at time `t`** iff:

- `active_windows` is empty, OR
- `t` (in the configured timezone) falls within any parsed window interval.

At route selection time, `RoutePool.get()` filters candidates by three conditions, all of which must hold:

1. Not in per-route cooldown.
2. Not in per-route quota hold.
3. Its `base` is active right now.

### Exhaustion behavior

Each candidate carries two independent boolean flags at decision time:

- `window_ok` — `is_active(now, windows, tz)` for this candidate's base.
- `timer_ok` — both cooldown and quota futures are `≤ now`.

`available` = candidates with `window_ok AND timer_ok`. When `available` is empty:

| Situation | Behavior |
|-----------|----------|
| **At least one candidate has `window_ok = True`** (i.e. time-of-day is OK, it's only cooldown/quota blocking) | Sleep until the minimum cooldown/quota expiry **among `window_ok` candidates only**, then retry the loop. Window-blocked candidates do not contribute to the wait time. |
| **Every candidate has `window_ok = False`** (regardless of their timer state) | Raise `ProviderOutOfActiveWindow` immediately. Do not sleep. |

Rationale: cooldown and quota are short (seconds to minutes); windows can be hours. Blocking a job that long is usually not what the caller wants, and if it is, the caller can catch the exception and retry. A candidate that is simultaneously window-blocked and cooldown-blocked is still fundamentally window-blocked — its cooldown means nothing if the window never opens in this run.

`ProviderOutOfActiveWindow` includes:

- The list of URLs involved.
- The earliest `next_active_start` among them, formatted in that URL's configured timezone.

### Uniform across provider types — but two code paths

Route selection has **two** entry points, both must be updated:

1. **`RoutePool.get()`** — the async, retrying path used by the paper extract pipeline, embedding pipeline, rerank pipeline, and snapshot advanced pipeline. Supports sleep-and-retry for timer-blocked candidates.
2. **`select_runtime_route()`** (`paper/routing.py:499`) — a synchronous one-shot selector used by `translator/cli.py:332/353/385`, `recognize/cli.py:1198/1508`, and `utils/cli.py:258`. It currently reaches into `pool._candidates` and runs `choose_weighted` directly, **never calling `get()`**. This path cannot sleep (no event loop context), so the contract is: filter out window-inactive candidates; if nothing remains, raise `ProviderOutOfActiveWindow`; otherwise run weighted selection over the survivors. Cooldown/quota state is not consulted here (it never was).

Both paths share the same new helpers (`_parse_windows_for_candidates`, `is_active`, `next_active_start`) and the same exception type.

### `resolve_active()` is unchanged

`EmbeddingConfig.resolve_active()` and `RerankConfig.resolve_active()` still resolve by name only — they do not do time checks. The runtime gate lives in `RoutePool`. This keeps config resolution pure and deterministic.

## Architecture

### New module: `paper/active_window.py`

Pure functions, no dataclass dependencies:

```python
def parse_windows(raw: list[str]) -> list[tuple[time, time]]:
    """Parse "HH:MM-HH:MM" strings; split cross-midnight into two intervals.
    Empty input returns []. Invalid input raises ValueError."""

def is_active(now: datetime, windows: list[tuple[time, time]], tz: tzinfo | None) -> bool:
    """Empty windows returns True. Otherwise convert `now` into `tz` (if `tz`
    is None, fall back to the system local zone — never to `now.tzinfo`),
    then check whether the resulting time-of-day falls in any [start, end)
    interval."""

def next_active_start(now: datetime, windows: list[tuple[time, time]], tz: tzinfo | None) -> datetime | None:
    """Returns the next datetime (in `tz` or system local if None) at which
    the URL becomes active. If now is already active, returns `now` unchanged.
    Empty windows returns None."""
```

### Config changes: `paper/config.py`

`BaseConfig` gains:

```python
active_windows: list[str] = field(default_factory=list)
active_timezone: str | None = None
```

Kept as raw strings inside the frozen dataclass. Parsing happens in `_parse_base_configs` for early validation; the parsed form is recomputed (cheaply, cached on `RoutePool`) at runtime. This avoids putting non-hashable `list[tuple[time, time]]` or `ZoneInfo` inside a frozen dataclass.

`_parse_base_configs` (≈`config.py:468`) reads the two new fields, runs `parse_windows` + `ZoneInfo` to validate, and stores the originals back.

### Routing changes: `paper/routing.py`

- Add `ProviderOutOfActiveWindow(RuntimeError)`.
- **Fix `_build_route_candidates` to preserve the new fields.** Currently (routing.py:227) it reconstructs `BaseConfig(url=base.url, weight=base.weight, key=[key])`, dropping any other fields. Update this constructor call to also pass `active_windows=base.active_windows` and `active_timezone=base.active_timezone`. Without this fix, every subsequent window check would see empty windows.
- `RoutePool.__init__` pre-parses each candidate's windows once (via `active_window.parse_windows`) and resolves the `ZoneInfo` (or system local zone fallback) keyed by `route_id`.
- `RoutePool.__init__` accepts an optional `now_provider: Callable[[], float]` defaulting to `time.time` — this is needed for deterministic tests. Only replaces `time.time()` wall-clock calls; `time.monotonic()` used for cooldown timestamps is left alone.
- `RoutePool.get()`:
  1. Compute `now_epoch` via the injected provider; derive `now_dt = datetime.fromtimestamp(now_epoch, tz=timezone.utc)` (astimezone is handled inside `is_active`).
  2. For each candidate, compute `window_ok = is_active(now_dt, parsed_windows[rid], tz[rid])` and `timer_ok = (cooldown <= now_mono and quota_until <= now_epoch)`.
  3. `available` = candidates where both flags are true; if non-empty, weighted-pick one and return.
  4. If empty: check if **any** candidate has `window_ok = True`.
     - Yes → compute minimum cooldown/quota wait among `window_ok` candidates; sleep; loop. (Window-blocked candidates contribute nothing to the wait.)
     - No → collect URLs from all candidates, compute `earliest = min(next_active_start(now_dt, windows[rid], tz[rid]))`, raise `ProviderOutOfActiveWindow(urls, earliest)`.

- **`select_runtime_route()`** (routing.py:499): after building candidates, filter them by `is_active(datetime.now(timezone.utc), parsed_windows, tz)`. If the filtered list is empty, raise `ProviderOutOfActiveWindow`. Otherwise pass the filtered list + their weights to `choose_weighted`.

### Exception propagation

- Pipeline / library code (`paper/extract.py`, `paper/embed_pipeline.py`, `paper/snapshot/advanced/pipeline.py`, `translator/engine.py`) does **not** catch `ProviderOutOfActiveWindow`. It propagates upward unchanged.
- Every CLI command that reaches the routing layer catches `ProviderOutOfActiveWindow` at its outermost boundary and converts it into `click.ClickException(str(exc))`. This gives users a clean one-line error and non-zero exit instead of a Python traceback. Implemented as a small shared `contextmanager` used at each entry point (see plan Task 4.1).
- Affected CLI commands (8 total; a single wrapper per command function catches both sync `select_runtime_route` raises and async `RoutePool.get()` raises):
  - `translator translate` (`translator/cli.py`)
  - `recognize fix-math`, `recognize fix-mermaid` (`recognize/cli.py`)
  - `utils test-mode` (`utils/cli.py`)
  - `paper extract`, `paper embed`, `paper search` (`paper/cli.py`)
  - `paper db generate-tags` (`paper/db.py`)
- Out of scope in this change:
  - `paper db api serve` is a long-running Starlette server; `ProviderOutOfActiveWindow` raised inside a request handler surfaces as an HTTP 500 via Starlette's default exception layer. No dedicated handler is added here. A follow-up can map it to a 503 with structured JSON if operators need it.
  - Other `asyncio.run` sites in `recognize/cli.py` (`md embed`, `md unpack`, `fix`) do not construct a `RoutePool` or call `select_runtime_route`, so `ProviderOutOfActiveWindow` cannot originate from them.

## Edge Cases

- **DST transitions**: `datetime.now(tz)` + `ZoneInfo` handles spring-forward / fall-back correctly. A window straddling 02:30 local may be skipped or doubled on a DST boundary; document it as expected. The project's primary zone (`Asia/Shanghai`) has no DST so this is mostly theoretical.
- **Multiple overlapping windows**: `["08:00-12:00", "10:00-15:00"]` is legal; union semantics. No normalization required.
- **Exactly at `end`**: right-open interval — `10:00-12:00` at `12:00:00.000` is out. Documented explicitly.
- **`24:00` as end boundary**: `"00:00-24:00"` is the canonical "all day" (equivalent to omitting `active_windows`, but allowed for explicitness). `"23:00-24:00"` means `[23:00, 24:00)` on the same day — *not* cross-midnight. `"24:00"` as a start boundary is always rejected.
- **Clock skew**: `time.time()` is wall-clock, subject to system clock changes. Same limitation applies to existing quota logic; out of scope.
- **Non-hashable fields in frozen dataclass**: avoided by storing raw `list[str]` and parsing elsewhere.

## Observability

- On transition "window opened" / "window closed": no log. Inferring state from request logs is enough.
- On raising `ProviderOutOfActiveWindow`: log `WARNING` once with the URL list and the earliest next-start timestamp.
- On sleep-wait that happens to coincide with window state (i.e. the candidate is both window-blocked and timer-blocked): no extra log beyond the existing "All weighted routes unavailable" message.

## Backward Compatibility

- Omitting both new fields is valid and keeps 24/7 behavior. No config migration needed.
- Dataclass has default-factory / `None` defaults; all existing constructors continue to work.
- No change to the public API surface of `PaperConfig`, `RoutePool`, or `resolve_active`.

## Testing

Follows CLAUDE.md black-box policy — tests see interface signatures and plain-language behavior, not implementation.

**Unit (`tests/test_active_window.py`):**

- `parse_windows`: empty, single, multi, cross-midnight, `start==end` rejected, invalid hour/minute rejected, malformed string rejected.
- `is_active`: inside window, on `start` (inclusive), on `end` (exclusive), cross-midnight late-night true, cross-midnight early-morning true, outside false, empty windows true.
- `next_active_start`: already active returns now, just past a window returns next window's start, cross-day returns next day's start, empty returns None.
- Timezone: active at 23:30 `Asia/Shanghai` with window `"23:00-24:00"` is true; same UTC instant is 15:30 UTC and a UTC-based check would report false — confirms tz path is exercised.

**Routing (`tests/test_routing_active_window.py`):**

- All routes in-window → weighted selection unchanged.
- Mix in/out → only in-window routes selected.
- All out-of-window → `ProviderOutOfActiveWindow` raised.
- Out-of-window + one route in cooldown → sleeps for cooldown, does not raise.
- Injects fake `now_provider` + asyncio event loop for deterministic behavior.

**Config loading (`tests/test_embedding_config.py` extension):**

- Invalid `active_windows` string → `ValueError` at config load.
- Invalid `active_timezone` → `ValueError` at config load.
- Valid windows round-trip through `PaperConfig`.

## Open Questions

None blocking. Noted future work:

- If users ask for calendar rules (weekdays only), this spec intentionally defers. A `cron_expr` field could be added without breaking `active_windows`.
- If users ask for "wait and retry on window", a per-pool `wait_on_window_block: bool` option can be added later without breaking the default raise behavior.
