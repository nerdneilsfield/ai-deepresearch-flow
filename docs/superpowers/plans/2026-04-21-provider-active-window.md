# Provider / URL Active Window — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional per-URL active-time windows to `BaseConfig` (shared by main providers, `embedding.providers`, and `rerank.providers`). At route-selection time, URLs outside their window are filtered out; when every URL in a `RoutePool` is only blocked by its window, the pool raises `ProviderOutOfActiveWindow` instead of sleeping.

**Spec:** `docs/superpowers/specs/2026-04-21-provider-active-window-design.md`

**Tech Stack:** Python ≥ 3.12, `dataclasses`, `zoneinfo`, `asyncio`, pytest.

**Back-compat:** Omitting the new fields keeps 24/7 behavior. No migration needed.

**Key invariants:**
- `active_windows == []` means "always active".
- Windows are `[start, end)`, 24-hour, cross-midnight allowed.
- Raw `list[str]` stays in `BaseConfig` (frozen, hashable-friendly); parsed forms live in `RoutePool`.

---

## Phase 1 — Pure window helpers

### Task 1.1: `active_window` module

**Files:**
- Create: `python/deepresearch_flow/paper/active_window.py`
- Test: `python/deepresearch_flow/paper/tests/test_active_window.py`

**Interface (given to the test-writing subagent; implementation not shared):**

```python
# module: deepresearch_flow.paper.active_window

def parse_windows(raw: list[str]) -> list[tuple[time, time]]
# Parse "HH:MM-HH:MM" strings. Start boundary: HH in 0-23, MM in 0-59.
# End boundary: HH in 0-23 with MM in 0-59, OR literal "24:00" (represented
# as time(0, 0) in the returned tuple — meaning end-of-day, not start-of-day).
# Cross-midnight "22:00-06:00" is split into two tuples:
#   (time(22,0), time(0,0))  # [22:00, 24:00) — end(0,0) means end-of-day
#   (time(0,0),  time(6,0))  # [00:00, 06:00)
# "00:00-24:00" is kept as a single tuple (time(0,0), time(0,0)) with end-of-day.
# "24:00" as a start is rejected. start == end rejected. Empty input returns [].
# ValueError on malformed / out-of-range values.

def is_active(now: datetime, windows: list[tuple[time, time]], tz: tzinfo | None) -> bool
# Empty windows returns True. Otherwise: if tz is None, fall back to the
# system local zone (NOT to now.tzinfo). Convert `now` into that zone, and
# return True iff the resulting time-of-day falls in any [start, end)
# interval. An end of time(0, 0) that came from "24:00" matches any
# time-of-day whose start-of-day >= start.

def next_active_start(now: datetime, windows: list[tuple[time, time]], tz: tzinfo | None) -> datetime | None
# Uses the same tz fallback as is_active. If currently active, returns `now`
# unchanged. Otherwise returns the next datetime at which a window opens.
# Empty windows returns None.
```

- [ ] **Step 1 (RED)**: Write `tests/test_active_window.py` covering:
  - `parse_windows`: empty, single `"08:00-12:00"`, cross-midnight `"22:00-06:00"` (returns 2 tuples), `"00:00-24:00"` (returns 1 tuple), `"23:00-24:00"` (NOT cross-midnight, 1 tuple), multi, `"12:00-12:00"` rejected, `"24:00-06:00"` rejected (24:00 as start), `"25:00-26:00"` rejected, `"abc"` rejected.
  - `is_active`: true at `start`, false at `end`, false outside, cross-midnight true at `23:30` and `03:00`, `"23:00-24:00"` true at `23:59`, `"00:00-24:00"` true at any time, empty true.
  - `is_active` tz fallback: `datetime(2026,4,21,15,30,tzinfo=timezone.utc)` with `tz=ZoneInfo("Asia/Shanghai")` (= local 23:30) active for window `"23:00-24:00"`; same input with `tz=None` on a machine where `TZ` is set to `Asia/Shanghai` also active. Test sets `TZ` via `monkeypatch.setenv` + `time.tzset()`.
  - `is_active` does NOT use `now.tzinfo` when `tz=None`: pass `datetime(2026,4,21,15,30,tzinfo=ZoneInfo("Asia/Shanghai"))` (aware dt in Shanghai showing 15:30) + `tz=None` under `TZ=UTC` → should be false for window `"15:00-16:00"` because fallback is system-local UTC 15:30 is out (and to prove it doesn't just use `now.tzinfo`). Actually simpler: any test that would give a different answer if the implementation cheated and used `now.tzinfo`.
  - `next_active_start`: already active → exactly `now`; just-past window → next window start same day; after final window → next day's first start; empty → None.

- [ ] **Step 2 (GREEN)**: Implement `paper/active_window.py`. Use `time(0,0)` sentinel for 24:00 when end-boundary; use `datetime.combine` + timezone conversion for `is_active` / `next_active_start`.

- [ ] **Step 3**: `uv run pytest python/deepresearch_flow/paper/tests/test_active_window.py -v` — all green.

---

## Phase 2 — Config layer

### Task 2.1: Extend `BaseConfig` + parser

**Files:**
- Modify: `python/deepresearch_flow/paper/config.py`
  - `BaseConfig` dataclass (line ~51)
  - `_parse_base_configs` helper (line ~468)
- Test: `python/deepresearch_flow/paper/tests/test_config_active_window.py` (new)

- [ ] **Step 1 (RED)**: Write `test_config_active_window.py`:
  - Loading a config with `active_windows = ["09:00-12:00"]` and `active_timezone = "Asia/Shanghai"` on a main provider's base → `paper_config.providers[0].base[0].active_windows == ["09:00-12:00"]`, `.active_timezone == "Asia/Shanghai"`.
  - Same applied to an `[[embedding.providers]]` base and a `[[rerank.providers]]` base.
  - Invalid `active_windows = ["13:00"]` → raises during `load_config` with message naming the offending path.
  - Invalid `active_timezone = "Not/A_Zone"` → raises during `load_config`.
  - Omitting both fields → `.active_windows == []`, `.active_timezone is None`, load succeeds.

- [ ] **Step 2 (GREEN)**:
  - Add to `BaseConfig`:
    ```python
    active_windows: list[str] = field(default_factory=list)
    active_timezone: str | None = None
    ```
  - In `_parse_base_configs`:
    - Read `item.get("active_windows")`; if present, must be `list[str]`. Run `active_window.parse_windows(...)` once for validation; keep originals.
    - Read `item.get("active_timezone")`; if present, must be `str`. Run `zoneinfo.ZoneInfo(name)` for validation; keep original string.
    - Pass both into `BaseConfig(...)`.

- [ ] **Step 3**: `uv run pytest python/deepresearch_flow/paper/tests/test_config_active_window.py -v` — all green. Also confirm existing `test_weighted_routing.py`, `test_embedding_config.py` still pass.

---

## Phase 3 — Routing layer

### Task 3.1: Preserve new `BaseConfig` fields through route expansion

**Files:**
- Modify: `python/deepresearch_flow/paper/routing.py` (`_build_route_candidates`, line ~227)

Context: `_build_route_candidates` currently rebuilds `BaseConfig(url=base.url, weight=base.weight, key=[key])` and drops every other field. Unless we also carry `active_windows` and `active_timezone` across, every runtime check will see empty windows.

- [ ] **Step 1 (RED)**: Add a unit test in `tests/test_weighted_routing.py` that asserts: after `_build_route_candidates(provider_with_windows, model, weight=1)`, every returned candidate's `route.base.active_windows` and `route.base.active_timezone` equal the original.

- [ ] **Step 2 (GREEN)**: Update the constructor call:
  ```python
  routed_base = BaseConfig(
      url=base.url,
      weight=base.weight,
      key=[key],
      active_windows=base.active_windows,
      active_timezone=base.active_timezone,
  )
  ```

- [ ] **Step 3**: `uv run pytest python/deepresearch_flow/paper/tests/test_weighted_routing.py -v` green.

### Task 3.2: `ProviderOutOfActiveWindow` exception + `now_provider` injection

**Files:**
- Modify: `python/deepresearch_flow/paper/routing.py`
  - Add exception class near top of module.
  - `RoutePool.__init__` signature (line ~247).

- [ ] **Step 1 (GREEN, structural — no new test, Task 3.3 will cover behavior)**:
  - Add:
    ```python
    class ProviderOutOfActiveWindow(RuntimeError):
        def __init__(self, urls: list[str], next_available: datetime | None) -> None: ...
    ```
    `str(exc)` formats as `"All provider URLs are outside their active window: [url1, url2]; next available at 2026-04-21 22:00:00+08:00"` (or `"unknown"` if `None`).
  - Extend `RoutePool.__init__` with `now_provider: Callable[[], float] | None = None`; store `self._now = now_provider or time.time`. Replace the `time.time()` calls in `get()` (not the `time.monotonic()` ones) with `self._now()`.
  - Update `RoutePool.from_selector`, `from_embedding_provider`, `from_rerank_provider`, `_from_active_route_config` to thread `now_provider` through (default `None`).

- [ ] **Step 2**: Existing tests still pass (`uv run pytest python/deepresearch_flow/paper/tests/test_weighted_routing.py -v`). No behavioral change yet.

### Task 3.3: Window filter + exhaustion classification in `RoutePool.get()`

**Files:**
- Modify: `python/deepresearch_flow/paper/routing.py` (`RoutePool.__init__`, `RoutePool.get`)
- Test: `python/deepresearch_flow/paper/tests/test_routing_active_window.py` (new)

- [ ] **Step 1 (RED)**: Write `test_routing_active_window.py`:

  Interface (given to the test-writing subagent):
  - `RoutePool.__init__(candidates, *, cooldown_seconds, verbose, rng, now_provider)` — `now_provider()` returns an epoch-seconds float used for all wall-clock time checks.
  - `await RoutePool.get()` returns a `RuntimeRoute` or raises `ProviderOutOfActiveWindow`.
  - Each `_RouteCandidate.route.base.active_windows` is a `list[str]`; `.base.active_timezone` is `str | None`.

  Cases:
  1. Two candidates, both windows `["00:00-24:00"]`, any frozen `now_provider` → `get()` returns one of them; no exception.
  2. Two candidates, one window `["00:00-24:00"]` and one `["22:00-23:00"]`, `now_provider` returning a Unix timestamp whose local Shanghai time falls in `[09:00, 10:00)` → `get()` always returns the first; never the second.
  3. Single candidate, window `["22:00-23:00"]` (tz Asia/Shanghai), `now_provider` at local `09:00` → `get()` raises `ProviderOutOfActiveWindow`; `str(exc)` contains the URL and `next_available` at `22:00` same day.
  4. Two candidates, **both out-of-window**, one additionally cooldown-blocked → `get()` raises `ProviderOutOfActiveWindow`. (No candidate has `window_ok=True`, so cooldown is irrelevant.)
  5. One candidate **in-window but cooling down**, one candidate **out-of-window, not cooling** → `get()` sleeps for the cooldown (short, e.g. 0.02s real wait via `cooldown_seconds=0.02` and a pre-seeded cooldown timestamp) and returns the in-window candidate after expiry. Never returns the out-of-window one.
  6. Mixed: candidate A in-window + cooldown 0.02s, candidate B out-of-window + cooldown 10s. Sleep duration must be based on A (0.02), not B (10). Assert total elapsed time < 1s.

- [ ] **Step 2 (GREEN)**: Modify `RoutePool.__init__`:
  - Build `self._windows: dict[str, list[tuple[time, time]]]` via `parse_windows(base.active_windows)` per candidate.
  - Build `self._tz: dict[str, tzinfo | None]` via `ZoneInfo(base.active_timezone)` when set, else `None` (helpers resolve to system local).

  Modify `RoutePool.get()`:
  - Compute `now_epoch = self._now()`, `now_mono = time.monotonic()`, `now_dt = datetime.fromtimestamp(now_epoch, tz=timezone.utc)`.
  - Per candidate compute `window_ok = is_active(now_dt, self._windows[rid], self._tz[rid])` and `timer_ok = cooldown[rid] <= now_mono and quota_until[rid] <= now_epoch`.
  - `available = [c for c in candidates if window_ok(c) and timer_ok(c)]`. If non-empty, weighted-pick and return.
  - If empty:
    - If **any** candidate has `window_ok = True` → compute minimum `max(cooldown_wait, quota_wait)` **restricted to the `window_ok` subset**; sleep; loop.
    - Else (no candidate has `window_ok = True`) → compute `earliest = min(next_active_start(now_dt, self._windows[rid], self._tz[rid]) for rid in all_candidates if next_active_start is not None)`; `raise ProviderOutOfActiveWindow(urls, earliest)`.

- [ ] **Step 3**: `uv run pytest python/deepresearch_flow/paper/tests/test_routing_active_window.py -v` — all green. `uv run pytest python/deepresearch_flow/paper/tests/test_weighted_routing.py -v` still green.

### Task 3.4: Apply window filter to `select_runtime_route` (synchronous one-shot path)

Context: `select_runtime_route` (routing.py:499) is used by `translator/cli.py:332/353/385`, `recognize/cli.py:1198/1508`, and `utils/cli.py:258`. It reads `pool._candidates` directly and calls `choose_weighted`, never entering `get()`. Without changes here, those CLIs bypass the window filter entirely.

**Files:**
- Modify: `python/deepresearch_flow/paper/routing.py` (`select_runtime_route`)
- Test: extend `tests/test_weighted_routing.py`

- [ ] **Step 1 (RED)**: Add tests to `test_weighted_routing.py`:
  - `select_runtime_route` with a config where all main-model candidates' bases have windows that don't include "now" → raises `ProviderOutOfActiveWindow`.
  - With one candidate in-window and one out → always returns the in-window route. Run 20 iterations to confirm the out-of-window one never gets picked.
  - Default no-windows config → behavior unchanged.

- [ ] **Step 2 (GREEN)**: Inside `select_runtime_route`, after building the pool:
  ```python
  now_dt = datetime.now(timezone.utc)
  filtered = [
      c for c in pool._candidates
      if is_active(now_dt,
                   parse_windows(c.route.base.active_windows),
                   ZoneInfo(c.route.base.active_timezone) if c.route.base.active_timezone else None)
  ]
  if not filtered:
      urls = [c.route.base.url for c in pool._candidates]
      earliest = min((next_active_start(now_dt, ...) for c in pool._candidates), default=None)
      raise ProviderOutOfActiveWindow(urls, earliest)
  return choose_weighted([c.route for c in filtered], [c.weight for c in filtered], rng=rng)
  ```
  (Extract the `parse_windows` / `ZoneInfo` resolution into a small helper to share with `RoutePool.__init__`.)

- [ ] **Step 3**: `uv run pytest python/deepresearch_flow/paper/tests/test_weighted_routing.py -v` green.

---

## Phase 4 — CLI integration, docs, example config

### Task 4.1: Convert `ProviderOutOfActiveWindow` to `ClickException` at every CLI entry point

Context: `ProviderOutOfActiveWindow` is a `RuntimeError`. Without catching it at the CLI boundary, users see a Python traceback — contradicting spec §Exception propagation ("CLI prints a clear message and exits non-zero"). Every Click command that either calls `select_runtime_route` or constructs a `RoutePool` must convert the exception.

**Authoritative list of commands to wrap** (`@click.command`-decorated functions; one wrapper per function covers both sync and async routing calls inside it):

| # | File | Command | Routing usage |
|---|------|---------|---------------|
| 1 | `translator/cli.py` | `translate` (line ~250) | `select_runtime_route` at :332, :353, :385 |
| 2 | `recognize/cli.py` | `fix-math` (line :1104) | `select_runtime_route` at :1198 + `RoutePool.from_selector` at :1201 + `asyncio.run` at :1386 |
| 3 | `recognize/cli.py` | `fix-mermaid` (line :1414) | `select_runtime_route` at :1508 + `RoutePool.from_selector` at :1511 + `asyncio.run` at :1831 |
| 4 | `utils/cli.py` | `test-mode` (line ~250) | `select_runtime_route` at :258 |
| 5 | `paper/cli.py` | `extract` (line :365) | `asyncio.run` at :580 drives main routing |
| 6 | `paper/cli.py` | `embed` (line :634) | `asyncio.run` at :690 drives embedding routing |
| 7 | `paper/cli.py` | `search` (line :718) | `asyncio.run` at :740 drives embedding + rerank routing |
| 8 | `paper/db.py` | `generate-tags` (line :1763) | `RoutePool.from_selector` at :1773 + `asyncio.run(_run())` at :1805 |

Out of scope for this task (separate handling):

- **`paper db api serve`** (`paper/db.py:888`, pools created at :1022/:1025). Long-running server; `ProviderOutOfActiveWindow` surfaces inside Starlette request handlers, not at CLI exit. Spec §Exception propagation notes this; we rely on Starlette's default 500 response (the message string is safe to include). No extra handler added in this task — follow-up if operators complain.
- Other `asyncio.run` sites in `recognize/cli.py` (`md embed` :690, `md unpack` :765, `fix` :1044/:1055). These do not create `RoutePool` or call `select_runtime_route`, so `ProviderOutOfActiveWindow` cannot originate from them. Confirmed by grep in planning.

**Files:**
- Modify: `paper/routing.py` (new helper), `translator/cli.py`, `recognize/cli.py`, `utils/cli.py`, `paper/cli.py`, `paper/db.py`.
- No new test file; smoke-verify manually in Phase 5.

- [ ] **Step 1**: Add a small shared helper in `paper/routing.py`:
  ```python
  from contextlib import contextmanager

  @contextmanager
  def provider_window_error_as_click():
      try:
          yield
      except ProviderOutOfActiveWindow as exc:
          import click
          raise click.ClickException(str(exc)) from exc
  ```

- [ ] **Step 2**: For each command in the table above, wrap its full body in `with provider_window_error_as_click(): ...`. Place the `with` at the very top of the command function so it encloses both sync `select_runtime_route` calls and any `asyncio.run(...)` inside. Do NOT add `except` clauses inside the pipeline coroutines — keep the boundary at the CLI layer.

- [ ] **Step 3**: Verify each command still passes non-window errors through unchanged (existing `click.ClickException` and `RuntimeError` code paths): rely on existing test suites.

- [ ] **Step 4**: Automated regression for the wrapper itself (so we don't only rely on Phase 5 smoke).

  Add `python/deepresearch_flow/paper/tests/test_cli_window_wrapper.py`:
  - **Direct contract test** of `provider_window_error_as_click`:
    - Entering the context manager, raising `ProviderOutOfActiveWindow(["u"], some_dt)` inside → `click.ClickException` is raised; `exc.message` equals `str(original)`; `exc.exit_code == 1` (Click default).
    - Entering, raising an unrelated `RuntimeError("x")` → re-raised as `RuntimeError`, NOT wrapped.
    - Entering, raising `click.ClickException("already wrapped")` → passes through unchanged.
  - **Command-level integration test** using `click.testing.CliRunner` against at least one command per CLI module that we wrapped. Use `monkeypatch` to replace `select_runtime_route` (or `RoutePool.from_selector`) with a stub that raises `ProviderOutOfActiveWindow`. Assert:
    - `result.exit_code == 1`
    - `result.output` ends with a single `Error: All provider URLs are outside their active window: ...` line
    - `result.exception` is `SystemExit` (Click converts ClickException to SystemExit), NOT `ProviderOutOfActiveWindow` — this is the anti-traceback assertion.
  - Coverage: one case per CLI module is enough (`translator translate`, `recognize fix-math`, `utils test-mode`, `paper embed`, `paper db generate-tags`). Don't duplicate across all 8 commands — the wrapper is the same.

- [ ] **Step 5**: Light regression:
  - `uv run pytest python/deepresearch_flow/paper/tests/ -q`
  - `uv run pytest python/deepresearch_flow/translator/tests/ -q`
  - `uv run pytest python/deepresearch_flow/recognize/tests/ -q` (if present)
  All green.

### Task 4.2: Update `config.example.toml`

**Files:**
- Modify: `config.example.toml`

- [ ] Add a commented-out `active_windows` + `active_timezone` line in the Ollama `[[providers]]` block (line 89–101) and in the Ollama `[[embedding.providers]]` block (line 116–125). Example in spec §Example.

- [ ] Add a short paragraph above `[[providers]]` (line ~41) noting the optional window fields, pointing to the spec doc.

### Task 4.3: No README changes this pass

Rationale: README is user-facing; feature is advanced. Defer doc surface until at least one real user requests it.

---

## Phase 5 — Smoke verification

- [ ] **Step 1**: `uv run pytest python/deepresearch_flow/paper/tests/ -v` all green.
- [ ] **Step 2**: `uv run mypy python/deepresearch_flow/paper/config.py python/deepresearch_flow/paper/routing.py python/deepresearch_flow/paper/active_window.py` — no new errors.
- [ ] **Step 3**: Manual (commands confirmed against `paper/cli.py`):
  1. **Async path** — `paper embed`: in `config.toml`, set the `[[embedding.providers]]` ollama base to `active_windows = ["00:00-00:01"]`. Run `uv run deepresearch-flow paper embed --config config.toml <some_input>` (see `paper/cli.py:622`). Expect a clean one-line error ending with `Error: All provider URLs are outside their active window: ...` (thanks to Task 4.1 wrapping), not a Python traceback.
     - Note: `paper search` (`cli.py:706`) preflights the vector dir (`cli.py:737`) **before** touching the embedding provider, so it will fail with `Vector index not found` instead of the window error unless an existing vector dir is present. Skip `paper search` in this smoke unless a vector dir already exists; `paper embed` alone covers the async window path.
  2. **Sync path** — `translator translate`: set the main ollama `[[providers]]` base to `active_windows = ["00:00-00:01"]`. Run any command that hits `select_runtime_route`, e.g. `uv run deepresearch-flow translator translate --config config.toml ...`. Expect the same clean one-line error, not a traceback.
  3. **Positive path**: change to `active_windows = ["00:00-24:00"]` on both bases. Both commands succeed.
  4. **Timezone path**: change to `active_windows = ["22:00-06:00"]`, `active_timezone = "Asia/Shanghai"`. Verify current local-time behavior matches expectation (pass at night, fail during day).
- [ ] **Step 4**: Revert any manual-test edits to `config.toml` before committing.

---

## Follow-ups (out of scope for this plan)

- **`paper db api serve` structured error response**: the app is constructed with default `Starlette(...)` settings (`paper/snapshot/api.py:1065`). When a request lands while all routes are out-of-window, `ProviderOutOfActiveWindow` propagates into Starlette's default exception path, which — with `debug=False` (the default) — returns a generic `500 Internal Server Error` body and logs the traceback server-side; the exception's URL list and next-available timestamp do not reach the client. A follow-up task should register a Starlette exception handler for `ProviderOutOfActiveWindow` that returns a structured 503 (`{"error": "out_of_active_window", "next_available": "..."}`) plus a `Retry-After` header. Tracked here rather than silently deferred.

## Risks / Notes

- **Frozen dataclass**: `list[str]` stays inside `BaseConfig`; tests that compare instances by equality still work. Don't mutate the list after construction.
- **tzdata**: `zoneinfo.ZoneInfo` needs system tzdata (always present on Linux/WSL). If the project starts supporting Windows Python envs, add `tzdata` to `pyproject.toml` dependencies. Not done in this plan.
- **DST**: Asia/Shanghai has none; other zones may exhibit "skipped" or "doubled" minutes on DST boundaries. Spec §Edge Cases documents this as expected.
- **Test clock injection**: Only `time.time()` is replaced with `self._now`. `time.monotonic()` (used for cooldowns) is left alone — cooldown tests use real short sleeps (≤ 0.05s).
