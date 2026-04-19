# MCP Granular Read + Reusable Authorization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add reusable bearer-token authorization for MCP and introduce granular MCP reads for summary keys and markdown outline/line ranges without breaking existing full-content tools.

**Architecture:** Extract bearer verification into a snapshot-level auth module that can be reused by advanced search and MCP transport mounts. Keep existing MCP full reads intact, then add new lightweight tools backed by helper utilities for summary-key traversal and markdown outline/line slicing. Set the shared MCP default truncation ceiling to `8000` characters and cap summary-key previews at `80` Unicode code points so larger payloads always require an explicit opt-in.

**Tech Stack:** Python, Starlette, FastMCP, Click, shared snapshot helpers, pytest, README docs.

---

### Task 1: Extract shared snapshot auth primitives

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/auth.py`
- Modify: `python/deepresearch_flow/paper/snapshot/advanced/auth.py`
- Inspect or modify only if import shape changes: `python/deepresearch_flow/paper/snapshot/advanced/handler.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_auth.py`
- Test: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- shared bearer verification accepts `Authorization: Bearer <token>` when the token matches
- missing header is rejected with reason `missing`
- malformed or wrong bearer token is rejected with reason `invalid`
- the shared ASGI wrapper passes requests through unchanged when no token is configured
- the shared ASGI wrapper bypasses auth for `OPTIONS` but enforces bearer auth for other HTTP methods when a token is configured
- any advanced-search auth test that imports the old module still observes the same input/output behavior after the refactor

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_auth.py python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py`

Expected:
- the new `python/deepresearch_flow/paper/snapshot/tests/test_auth.py` fails because the shared snapshot auth module does not exist yet
- the existing advanced-search auth tests stay green, proving current behavior before the refactor

**Step 3: Write minimal implementation**

Implement in `snapshot/auth.py`:
- one shared bearer-auth exception carrying `reason`
- one shared `verify_bearer(header_value, expected)` function
- keep the comparison constant-time
- one shared ASGI auth wrapper with no-token pass-through and `OPTIONS` bypass behavior

Update advanced-search auth import paths so advanced search uses the shared verifier instead of owning a duplicate implementation.

If `advanced/auth.py` can be reduced to a re-export shim, `advanced/handler.py` should stay untouched. Only modify `advanced/handler.py` if the import surface changes enough to require it.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_auth.py python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py`

Expected: PASS

**Step 5: Commit**

Run:

```bash
git add python/deepresearch_flow/paper/snapshot/auth.py python/deepresearch_flow/paper/snapshot/advanced/auth.py python/deepresearch_flow/paper/snapshot/tests/test_auth.py python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py
git commit -m "refactor: share snapshot bearer auth"
```

### Task 2: Protect MCP transports with reusable bearer auth

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Modify: `python/deepresearch_flow/paper/snapshot/api.py`
- Modify: `python/deepresearch_flow/paper/db.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- `/mcp` returns unauthorized when an MCP token is configured and the request omits the bearer token
- `/mcp` accepts a valid bearer token for allowed transport methods
- `/mcp-sse` requires the same bearer token for the SSE `GET` handshake path
- `OPTIONS` requests still bypass auth
- when no MCP token is configured, mounted MCP endpoints preserve current behavior

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py`

Expected: failures because MCP mounts are currently public and the CLI/app wiring does not expose an MCP token.

**Step 3: Write minimal implementation**

Implement a reusable ASGI protection wrapper using the shared snapshot auth module, then:
- apply it to the mounted `/mcp` and `/mcp-sse` apps
- require bearer auth for every non-`OPTIONS` HTTP method when MCP auth is configured
- make auth run before transport semantics when MCP auth is configured so missing/invalid bearer yields HTTP 401 before transport-specific HTTP 405 handling
- preserve current transport method semantics only after a valid bearer token is present
- add `--mcp-access-token` and `MCP_ACCESS_TOKEN` support in `paper db api serve`
- thread `mcp_access_token` through `create_app(...)` into MCP mount creation

Test transport behavior with `httpx.ASGITransport` rather than a live server so `/mcp` and `/mcp-sse` auth checks stay deterministic and non-flaky.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py`

Expected: PASS

**Step 5: Commit**

Run:

```bash
git add python/deepresearch_flow/paper/snapshot/mcp_server.py python/deepresearch_flow/paper/snapshot/api.py python/deepresearch_flow/paper/db.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py
git commit -m "feat: add bearer auth for mcp transports"
```

### Task 3: Add summary-key discovery and keyed summary reads

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/mcp_content.py`
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- `get_paper_summary_keys(paper_id, template=None, max_depth=2, include_preview=False)` returns deterministic key paths and types for nested summary JSON
- `get_paper_summary_key(paper_id, key, template=None, max_chars=None)` returns only the addressed node
- object and array nodes are addressable by key path
- invalid key syntax and missing key paths return the documented MCP errors
- summary field names containing `.`, `[` or `]` are treated as invalid key syntax rather than escaped
- `include_preview=True` never returns previews longer than `80` Unicode code points
- key ordering follows source document order rather than lexicographic sort

For test descriptions, specify only:
- module path
- function name
- parameter types
- return types
- expected observable behavior

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py`

Expected: failures because the content helper module and new MCP tools do not exist yet.

**Step 3: Write minimal implementation**

Create helper utilities for:
- summary key-path parsing
- summary key-path enumeration
- selected-node extraction and serialization

Set `_DEFAULT_MAX_CHARS = 8000` for MCP content-returning reads and keep explicit positive `max_chars` overrides available.
Apply that shared omitted-value ceiling to both new granular reads and existing string-returning MCP tools, even when config raises `max_chars_default` higher.
Preserve the legacy `[truncated: N more chars]` marker on existing string-returning tools when truncation happens.
Add a regression assertion that omitted `max_chars` keeps the content body capped at `8000`, while legacy string tools still expose truncation via the marker.

Register the new MCP tools in `mcp_server.py` while keeping the existing full-text tool shapes unchanged.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py`

Expected: PASS

**Step 5: Commit**

Run:

```bash
git add python/deepresearch_flow/paper/snapshot/mcp_content.py python/deepresearch_flow/paper/snapshot/mcp_server.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py
git commit -m "feat: add keyed summary reads for mcp"
```

### Task 4: Add markdown outline and line-range MCP tools

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_content.py`
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py`

**Dependency note:**
- Task 4 is sequentially after Task 3 because both tasks extend `mcp_content.py`, `mcp_server.py`, and the same test files.

**Step 1: Write the failing tests**

Add black-box tests covering:
- `get_paper_source_outline(paper_id)` reports section titles, levels, and line ranges
- `get_paper_source_lines(paper_id, start_line, end_line)` returns only the requested line slice and reports actual returned bounds
- translation variants behave the same for `lang`
- invalid line ranges are rejected
- line slices beyond file bounds are clamped and reported correctly
- outline IDs follow the documented backend slug rule on golden heading samples

Golden heading samples should include:
- blank heading fallback to `section-{n}`
- repeated headings producing `-2` and `-3`
- pure CJK heading
- mixed CJK + digits + hyphen
- multi-space + punctuation stripping

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py`

Expected: failures because outline and line-slice helpers are not implemented and no MCP tools expose them.

**Step 3: Write minimal implementation**

Extend the helper module to:
- scan markdown lines for headings
- compute section start/end ranges deterministically
- slice 1-based inclusive line ranges with returned `actual_start_line`, `actual_end_line`, and `total_lines`
- generate outline IDs using one backend-defined slug helper locked by tests instead of copying frontend logic informally

Register:
- `get_paper_source_outline`
- `get_paper_source_lines`
- `get_paper_translation_outline`
- `get_paper_translation_lines`

Keep the full-content source and translation tools unchanged.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py`

Expected: PASS

**Step 5: Commit**

Run:

```bash
git add python/deepresearch_flow/paper/snapshot/mcp_content.py python/deepresearch_flow/paper/snapshot/mcp_server.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py
git commit -m "feat: add outline and line-range reads for mcp"
```

### Task 5: Update MCP documentation in English and Chinese

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`

**Step 1: Write the failing checks**

Define a manual verification checklist covering:
- MCP auth setup is documented
- new granular summary and markdown tools are documented
- old full-content tools are still documented as available
- MCP token, admin token, and advanced-search token are documented as separate credentials
- the default `max_chars` value of `8000` and preview cap of `80` are documented

**Step 2: Run the checks to verify docs are outdated**

Inspect the MCP section in both README files and confirm that neither bearer auth nor the new granular tools are documented yet.

Expected: missing auth and missing granular-tool coverage.

**Step 3: Write minimal documentation updates**

Update both README files to document:
- `Authorization: Bearer <token>` behavior for MCP when configured
- `--mcp-access-token` / `MCP_ACCESS_TOKEN`
- MCP token vs advanced-search token vs admin token as independent controls
- new summary key tools
- new source/translation outline and line-range tools
- default `max_chars = 8000` and preview cap `80`
- the unchanged full-content tools

**Step 4: Verify the docs**

Re-read the MCP section in both files and confirm the auth and granular-read guidance is complete and internally consistent.

Expected: both README files describe the new auth flow and the new lightweight MCP tools clearly.

**Step 5: Commit**

Run:

```bash
git add README.md README_ZH.md
git commit -m "docs: describe mcp auth and granular reads"
```

### Task 6: Run focused regression verification

**Files:**
- Inspect only

**Step 1: Run focused test suite**

Run: `uv run pytest -q python/deepresearch_flow/paper/snapshot/tests/test_auth.py python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py`

Expected: PASS

**Step 2: Commit any final test or doc adjustments**

Run:

```bash
git add python/deepresearch_flow/paper/snapshot/tests/test_auth.py python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py python/deepresearch_flow/paper/snapshot/tests/test_mcp_content.py README.md README_ZH.md
git commit -m "test: verify mcp auth and granular read flow"
```
