# Semantic Push Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a dedicated `paper db api push-semantic` command that pushes only semantic rows from an existing local embed DB and supports chunk-window selection without truncating remote groups.

**Architecture:** Add a sibling CLI command under `paper db api` that reads all local semantic rows, sorts them deterministically, slices by chunk index, expands selected rows back to full `(doc_id, template_tag)` groups, batches them with the existing semantic push helpers, and then pushes them to the remote admin API. Retry mode replays existing semantic request payloads and is intentionally kept separate from chunk-window selection.

**Tech Stack:** Click, LanceDB helper layer, existing remote semantic push helpers, pytest, CliRunner.

---

### Task 1: Document and test the CLI contract

**Files:**
- Modify: `python/deepresearch_flow/paper/tests/test_db_api_push_cli.py`

**Step 1: Write the failing tests**

Add black-box CLI tests covering:

- `paper db api push-semantic` accepts `--embed-db` and `--config`
- `--start-chunk-idx` / `--end-chunk-idx` are 0-based, end-exclusive
- the selected chunk window expands to full groups before push
- `--retry-failed` replays semantic retry requests
- static retry reports are rejected
- `--retry-failed` cannot be combined with chunk-range flags
- invalid chunk-range values are rejected
- empty selected ranges exit cleanly without pushing

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest -q python/deepresearch_flow/paper/tests/test_db_api_push_cli.py -k "push_semantic"
```

Expected: FAIL because the new command does not exist yet.

**Step 3: Commit tests only after they pass later**

Do not commit in RED.

### Task 2: Add the semantic-only command

**Files:**
- Modify: `python/deepresearch_flow/paper/db.py`

**Step 1: Write minimal implementation**

Add `paper db api push-semantic` with:

- `--embed-db`
- `--config`
- `--retry-failed`
- `--start-chunk-idx`
- `--end-chunk-idx`

Validation:

- `--start-chunk-idx >= 0`
- `--end-chunk-idx == -1 or >= 0`
- `--retry-failed` and chunk-range flags are mutually exclusive
- static retry reports are rejected for this command

**Step 2: Add helper logic**

Inside `db.py`, add small helpers for:

- deterministic chunk sorting
- chunk-window slicing
- expansion from selected rows to full `(doc_id, template_tag)` groups
- retry report validation for semantic-only mode

**Step 3: Reuse existing push helpers**

Build the final semantic request list with:

- `load_index_meta`
- `open_store`
- `read_all_chunks`
- `group_chunks_for_push`
- `push_semantic_chunks`

Replay retry reports by passing their stored request payloads directly to `push_semantic_chunks(...)`.

**Step 4: Run focused tests**

Run:

```bash
uv run pytest -q python/deepresearch_flow/paper/tests/test_db_api_push_cli.py -k "push_semantic"
```

Expected: PASS

**Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/db.py
git commit -m "feat(api): add semantic-only push command"
```

### Task 3: Run full CLI regression for API push commands

**Files:**
- Verify only

**Step 1: Run full CLI test file**

Run:

```bash
uv run pytest -q python/deepresearch_flow/paper/tests/test_db_api_push_cli.py
```

Expected: PASS

**Step 2: Commit tests**

```bash
git add python/deepresearch_flow/paper/tests/test_db_api_push_cli.py
git commit -m "test(api): cover semantic-only push command"
```

### Task 4: Manual smoke guidance

**Files:**
- Verify only

**Step 1: Exercise range selection**

Run:

```bash
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./test-data/world_model-embed.db \
  --config remote.toml \
  --start-chunk-idx 0 \
  --end-chunk-idx 100
```

Confirm:

- the command prints the selected chunk window
- it reports the expanded group count
- it reports the final semantic request count

**Step 2: Exercise retry mode**

Run:

```bash
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./test-data/world_model-embed.db \
  --config remote.toml \
  --retry-failed ./push-semantic-errors.json
```

Confirm:

- the command accepts the semantic retry report
- no `KeyError: 'path'` occurs

**Step 3: Report residual risks**

If manual smoke is skipped, state that clearly in the final summary.
