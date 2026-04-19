# MCP Granular Read + Reusable Authorization Design

## Context

The current MCP surface in [mcp_server.py](/home/dengqi/Source/langs/python/ai-deepresearch-flow/python/deepresearch_flow/paper/snapshot/mcp_server.py:467) is optimized for convenience, not for transport cost.

Today:

- `get_paper_summary(...)` returns the full summary JSON payload as raw text.
- `get_paper_source(...)` returns the full source markdown body.
- `paper://.../summary`, `paper://.../source`, and `paper://.../translation/...` expose the same full-content behavior.
- the default truncation ceiling is still large enough to make MCP responses expensive for LLM clients.

At the same time, MCP is currently mounted as a public read surface in [api.py](/home/dengqi/Source/langs/python/ai-deepresearch-flow/python/deepresearch_flow/paper/snapshot/api.py:1006), while bearer-token verification already exists in the advanced search stack under [advanced/auth.py](/home/dengqi/Source/langs/python/ai-deepresearch-flow/python/deepresearch_flow/paper/snapshot/advanced/auth.py:1). That logic is too narrowly scoped to be reused by MCP or future protected endpoints.

The requested change has two parts that should land together:

1. keep the existing full MCP reads, but add precise reads by summary key and markdown line range
2. extract bearer-token auth into a reusable snapshot-level abstraction and apply it to MCP

## Requirements

- Existing MCP tools and resources continue to work. This is an additive change.
- MCP gains summary-key discovery and summary-key read tools.
- MCP gains markdown outline and markdown line-range read tools for source and translation content.
- MCP authorization uses `Authorization: Bearer <token>`.
- MCP authorization is optional. If no MCP token is configured, current public behavior remains unchanged.
- MCP content-returning tools use `8000` as the default `max_chars` ceiling unless the caller overrides it explicitly.
- `get_paper_summary_keys(..., include_preview=True)` caps each preview at `80` Unicode code points.
- The bearer-token validation primitive is no longer advanced-search-specific.
- The auth abstraction is reusable by advanced search and future snapshot HTTP surfaces.
- No snapshot schema migration is introduced.
- No static export rebuild format is introduced.

## Non-Goals

- Removing or redefining the existing full-content MCP tools.
- Replacing `paper://...` resources with granular resources.
- Persisting section indexes or line maps into SQLite or static manifests.
- Building a full markdown AST or HTML-rendered outline server-side.
- Unifying MCP token and advanced-search token into one required credential.

## Recommended Approach

Use one cohesive change set with three layers:

1. a reusable snapshot auth module
2. mount-level MCP bearer protection
3. new granular MCP tools for summary keys and markdown outline/line reads

This keeps compatibility stable while solving both transport-cost and access-control issues in one pass.

## Auth Abstraction

### New snapshot-level auth module

Create a new module under `python/deepresearch_flow/paper/snapshot/auth.py`.

It should own the generic bearer-token behavior currently embedded in advanced search:

- `verify_bearer(header_value: str | None, expected: str) -> None`
- one small auth exception carrying `reason = "missing" | "invalid"`
- one thin ASGI wrapper for protecting mounted apps with bearer auth

The low-level verifier stays transport-agnostic. It only checks the header format and token equality using constant-time comparison.

The ASGI wrapper owns HTTP concerns:

- only intercept `scope["type"] == "http"`
- bypass auth when no token is configured
- bypass `OPTIONS`
- require bearer auth for every other HTTP method, including the SSE `GET` handshake path
- return HTTP 401 with `WWW-Authenticate: Bearer` on failure

### Why mount-level auth

MCP auth belongs at the HTTP transport boundary, not inside individual tools.

Reasons:

- tool functions like `get_paper_summary(...)` are still useful in direct tests
- auth should block the request before FastMCP protocol handling starts
- SSE handshake protection needs HTTP-level enforcement, not tool-level checks
- wrapping the mounted MCP app keeps the read-only tool logic unchanged

## MCP Authorization Integration

### Token source

MCP should use a dedicated token, not the advanced-search token.

Recommended inputs:

- CLI option: `--mcp-access-token`
- env var: `MCP_ACCESS_TOKEN`

The snapshot API entrypoint in [db.py](/home/dengqi/Source/langs/python/ai-deepresearch-flow/python/deepresearch_flow/paper/db.py:888) should pass the resolved token into `create_app(...)`, which then threads it into the mounted MCP apps.

This token is independent from:

- advanced search token: `--search-access-token` / `SEARCH_ACCESS_TOKEN`
- admin token: `--admin-token` / `PAPER_DB_ADMIN_TOKEN`

The README must state explicitly that these three tokens protect different surfaces and are not implicitly shared.

### HTTP behavior

Protected endpoints:

- `/mcp`
- `/mcp-sse`

Behavior:

- if MCP token is unset, both endpoints behave exactly as they do now
- if MCP token is set, every non-`OPTIONS` HTTP request to `/mcp` and `/mcp-sse` requires `Authorization: Bearer <token>`
- `OPTIONS` remains open
- when MCP token is configured, auth runs before transport semantics, so missing/invalid bearer returns HTTP 401 before any transport-specific HTTP 405 handling is reached
- when a valid bearer token is present, transport-specific method behavior remains unchanged

This matters most for `/mcp`: with auth configured, unauthenticated `GET /mcp` returns 401 rather than transport-level 405. Once the request carries a valid bearer token, streamable HTTP still rejects unsupported methods in the normal way. `/mcp-sse` continues to allow `GET` handshake, but that handshake is protected whenever auth is enabled.

### Reuse by advanced search

Advanced search should stop owning its own auth primitive and instead import the shared verifier from `snapshot/auth.py`.

Its JSON error envelope in [handler.py](/home/dengqi/Source/langs/python/ai-deepresearch-flow/python/deepresearch_flow/paper/snapshot/advanced/handler.py:24) stays unchanged. Only the source of truth for bearer validation moves.

## Granular Summary Reads

### New tools

Add two new MCP tools:

- `get_paper_summary_keys(paper_id, template=None, max_depth=2, include_preview=False)`
- `get_paper_summary_key(paper_id, key, template=None, max_chars=None)`

Existing `get_paper_summary(...)` remains unchanged.

The only default-behavior tightening is truncation size: content-returning MCP tools should use `8000` as the default `max_chars` ceiling unless the caller overrides it explicitly.

### Key grammar

Use a predictable path grammar:

- object fields use dot access: `experiments.main_result`
- array elements use brackets: `contributions[0]`
- mixed paths are allowed: `limitations.items[1].title`

This is simple enough for MCP clients and deterministic enough for black-box testing.

To keep parsing simple and unambiguous, summary field names containing `.`, `[` or `]` are unsupported in the key grammar. The MCP tool should reject such key paths as invalid rather than introducing escaping rules. This is acceptable because the summary schemas used by this project do not define fields with those characters.

### `get_paper_summary_keys(...)`

This tool is discovery-oriented. It should not return the full summary body.

Recommended return shape:

```json
{
  "paper_id": "p1",
  "template": "deep_read",
  "root_type": "object",
  "paths": [
    {"key": "summary", "type": "string"},
    {"key": "contributions", "type": "array", "length": 3},
    {"key": "contributions[0]", "type": "string"},
    {"key": "experiments.main_result", "type": "string"}
  ]
}
```

Rules:

- preserve source document order by recursive traversal of the parsed JSON payload
- object keys follow source insertion order
- array items follow natural index order
- do not reorder paths lexicographically
- include arrays/objects as addressable nodes, not only leaf nodes
- `max_depth` caps traversal cost
- `include_preview=True` may add short previews for leaf strings, but each preview is capped at `80` Unicode code points rather than bytes

### `get_paper_summary_key(...)`

This tool returns only one addressed node.

Recommended return shape:

```json
{
  "paper_id": "p1",
  "template": "deep_read",
  "key": "experiments.main_result",
  "value_type": "string",
  "content_format": "text/plain",
  "content": "Top-1 improves by 2.3 points",
  "truncated": false
}
```

For objects and arrays:

- serialize the selected subtree to compact JSON text
- return `content_format = "application/json"`
- apply `max_chars` to the serialized text

This avoids mixed return types and keeps truncation semantics straightforward.

### Error model

Recommended new MCP tool errors:

- `invalid_summary_key`
- `summary_key_not_found`
- `template_not_available`

## Granular Markdown Reads

### New tools

Add four new MCP tools:

- `get_paper_source_outline(paper_id)`
- `get_paper_source_lines(paper_id, start_line, end_line)`
- `get_paper_translation_outline(paper_id, lang)`
- `get_paper_translation_lines(paper_id, lang, start_line, end_line)`

Existing `get_paper_source(...)` and translation resource behavior remain unchanged.

### Outline behavior

The outline tool exists to let clients discover section boundaries before requesting lines.

Recommended return shape:

```json
{
  "paper_id": "p1",
  "total_lines": 842,
  "sections": [
    {"id": "introduction", "title": "1 Introduction", "level": 1, "start_line": 12, "end_line": 58},
    {"id": "method", "title": "2 Method", "level": 1, "start_line": 59, "end_line": 168}
  ]
}
```

Implementation guidance:

- use a lightweight line scan over markdown text
- derive section ranges from heading positions
- keep IDs deterministic using a backend-defined slug rule documented here and locked by golden tests
- no HTML rendering on the server

The backend slug rule should be treated as the stable contract:

1. trim surrounding whitespace
2. lowercase
3. replace internal whitespace runs with `-`
4. keep ASCII letters, digits, CJK, Hiragana, Katakana, Hangul, and `-`
5. collapse repeated `-`
6. trim leading/trailing `-`
7. if the slug is empty, fall back to `section-{n}`
8. duplicate slugs append `-2`, `-3`, and so on

Implementation order matters:

1. whitespace replacement
2. character filtering
3. repeated-`-` collapse
4. leading/trailing `-` trim

This order ensures punctuation removal can still produce adjacent `-` runs that are then normalized correctly.

The frontend helper in [outline.ts](/home/dengqi/Source/langs/python/ai-deepresearch-flow/frontend/src/lib/outline.ts:1) should either be updated to match this rule exactly or be verified against the same golden samples so drift is caught by tests.

Suggested golden samples:

- heading: `"   "` with index `4` -> `section-4`
- headings: `"Introduction"`, `"Introduction"`, `"Introduction"` -> `introduction`, `introduction-2`, `introduction-3`
- heading: `"相关工作"` -> `相关工作`
- heading: `"  A/B  Test: 概览!  "` -> `ab-test-概览`

### Line-range behavior

Recommended return shape:

```json
{
  "paper_id": "p1",
  "start_line": 59,
  "end_line": 80,
  "actual_start_line": 59,
  "actual_end_line": 80,
  "total_lines": 842,
  "content": "## 2 Method\n..."
}
```

Rules:

- line numbers are 1-based and inclusive
- `start_line > end_line` is invalid
- out-of-bounds ranges may be clamped, but actual returned bounds must be included
- the tool returns only the requested slice, not the whole file with markers

### Why not resource URIs for ranges

Granular reads should be tool-only in this change.

Reasons:

- line ranges and key paths do not map cleanly onto static resource URIs
- bracketed key paths are awkward in URI templates
- this change is meant to reduce payload size, not to multiply URI variants

## Internal Helper Placement

Do not continue growing `mcp_server.py` as a monolith.

Recommended helper split:

- `snapshot/auth.py` for bearer verification and ASGI protection
- `snapshot/mcp_content.py` for:
  - summary key parsing and traversal
  - summary key listing
  - markdown heading outline extraction
  - markdown line slicing

`mcp_server.py` should remain the MCP registration layer plus static-asset loading glue.

## Configuration Surface

### API serve entrypoint

Update `paper db api serve` in [db.py](/home/dengqi/Source/langs/python/ai-deepresearch-flow/python/deepresearch_flow/paper/db.py:888) with:

- `--mcp-access-token`
- env fallback: `MCP_ACCESS_TOKEN`

This mirrors the existing admin and advanced-search token patterns without coupling them.

### App wiring

`create_app(...)` in [api.py](/home/dengqi/Source/langs/python/ai-deepresearch-flow/python/deepresearch_flow/paper/snapshot/api.py:993) should accept `mcp_access_token: str | None = None` and pass it into the MCP transport mounting path.

## Testing Strategy

All tests stay black-box.

Coverage areas:

- shared bearer verifier accepts valid bearer token and rejects missing/invalid header
- MCP mount auth protects `/mcp` and `/mcp-sse` when token is configured
- advanced search still validates bearer auth through the shared verifier
- summary keys tool lists expected key paths for nested summary JSON
- summary key tool returns only the addressed node
- source/translation outline tool returns deterministic line ranges
- source/translation line tool returns the requested slice and reported bounds
- backend slug generation stays stable on golden heading samples

Suggested test files:

- `python/deepresearch_flow/paper/snapshot/tests/test_auth.py`
- `python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py`
- `python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py`
- `python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py`

If the content helpers are split into a new module, add a dedicated black-box test file for that module under `snapshot/tests/`.

## Documentation Impact

Update both:

- `README.md`
- `README_ZH.md`

The MCP section should document:

- `Authorization: Bearer <token>` when MCP token is configured
- the new granular tools
- the continued existence of the old full-content tools

## Success Criteria

- MCP full-content tools still work unchanged
- MCP clients can discover summary keys without downloading full summary JSON
- MCP clients can fetch a single summary key or a markdown line range precisely
- MCP auth is optional, but when enabled protects both `/mcp` and `/mcp-sse`
- bearer verification is shared infrastructure, not advanced-search-local code
