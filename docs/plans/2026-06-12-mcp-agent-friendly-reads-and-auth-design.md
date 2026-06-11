# MCP Agent-Friendly Reads + Split Auth Entrypoints Design

## Context

The current paper snapshot MCP surface has already moved partway toward granular reads:

- summary discovery and keyed reads exist as `get_paper_summary_keys` and `get_paper_summary_key`
- source and translation markdown have outline and explicit line-range tools
- full-content MCP tools still exist and remain easy for an agent to call accidentally
- full-content MCP resources also exist and can bypass tool-level payload reduction
- GitHub OAuth currently shares the canonical `/mcp` streamable HTTP mount, while static bearer access is still needed for ordinary agent CLIs

The product goal is not strict backward compatibility. No external project depends on the old MCP tool names, resource names, or exact transport layout yet. The new surface should optimize for LLM clients that must avoid large payloads when reading paper source, translations, or structured summaries.

## Goals

1. Make partial reads the normal MCP workflow.
2. Remove or stop registering full-content MCP tools and resources that encourage oversized responses.
3. Replace source/translation-specific read tools with unified content tools.
4. Keep summary access key-oriented: discover keys first, then fetch only the selected value.
5. Add line-window modes beyond explicit ranges: `head`, `tail`, `head_tail`, and `around`.
6. Expose separate static bearer and GitHub OAuth MCP entrypoints when OAuth mode is enabled.
7. Keep static bearer entrypoints usable for ordinary CLI agents even when OAuth entrypoints are also exposed.
8. Keep implementation small: no schema migration, no precomputed line indexes, no static export format change.

## Non-Goals

- Maintaining old MCP tool names or resource URIs for compatibility.
- Returning entire source markdown, entire translation markdown, or entire summary JSON as a recommended MCP path.
- Building a markdown AST or semantic section parser.
- Implementing JSONPath fully. The existing simple summary-key grammar is enough.
- Changing paper snapshot database schema.
- Changing how static files are exported.
- Replacing GitHub OAuth with a general multi-user authorization system.
- Implementing summary string line windows in the core change; this remains follow-up scope.

## Public MCP Surface Policy

The public MCP surface includes both tools and resources. Removing a full-content tool is not enough if a `paper://...` resource still returns the same full content.

Core decision:

- unregister old full-content MCP tools
- unregister old full-content MCP resources and resource templates for summary, source, and translation
- keep internal loader/helper functions where useful for the new granular tools
- do not add replacement large resources in this change

Public behavior should be verified through MCP protocol calls only:

- `tools/list` includes new granular tool names and omits removed full-content tool names
- calling removed tool names returns the public MCP unknown-tool behavior
- `resources/list` does not advertise full-content paper summary/source/translation resources
- `resources/templates/list` does not advertise full-content paper summary/source/translation templates
- reading old full-content resource URIs fails with public resource-not-found behavior or is absent from the advertised list, depending on the MCP test harness capability

No test should inspect private FastMCP registries or rely on Python function presence/absence unless the helper is declared as a direct public Python API contract.

## Recommended MCP Tool Surface

### Keep search and metadata tools

Keep these existing tools because they are discovery and retrieval entrypoints rather than large-content readers:

- `search_papers`
- `search_papers_by_keyword`
- `filter_papers`
- `search_papers_semantic`
- `get_database_stats`
- `list_top_facets`
- `get_paper_metadata`
- `get_paper_bibtex`

Enhance `get_paper_metadata` to always include cheap availability fields:

- `has_source`: boolean
- `available_translations`: normalized language tags, list of strings
- existing `available_summary_templates`
- existing `has_bibtex`

These fields are mandatory in docs and tests, because they let agents decide which granular read tool to call next.

### Replace markdown read tools with `get_paper_content_outline`

Tool:

```python
def get_paper_content_outline(
    paper_id: str,
    content_type: Literal["source", "translation"],
    lang: str | None = None,
    max_sections: int = 200,
) -> dict[str, Any]
```

Behavior:

- `content_type="source"` loads source markdown.
- `content_type="source"` with non-null `lang` is invalid.
- `content_type="translation"` requires a non-empty `lang`.
- `lang` is a non-empty BCP-47-style tag made of ASCII letters/digits plus hyphen separators, for example `zh`, `zh-cn`, `en-us`, `pt-br`; underscores, whitespace, path separators, query characters, and empty strings are invalid.
- `lang` is normalized to lowercase before lookup. Metadata and lookup must use the same normalized tags. If normalization creates duplicate DB tags, treat the snapshot as invalid and return `translation_not_available` plus a diagnostic rather than choosing arbitrarily.
- Returns the same heading outline model used by the current source/translation outline helpers.
- `max_sections` must be an actual positive integer; booleans, strings, and floats are invalid.
- `max_sections` has a default of `200` and a hard ceiling of `500`.
- Sections are returned in document order. If capped, return the first `max_sections` headings plus `total_sections`, `returned_sections`, and `truncated=true`.

Return shape:

```json
{
  "paper_id": "p1",
  "content_type": "translation",
  "lang": "zh",
  "total_lines": 1200,
  "sections": [
    {"id": "abstract", "title": "Abstract", "level": 1, "start_line": 1, "end_line": 20}
  ],
  "total_sections": 12,
  "returned_sections": 12,
  "truncated": false
}
```

Errors:

- `invalid_content_type`
- `missing_lang`
- `invalid_lang_for_source`
- `invalid_lang`
- `invalid_section_count`
- `source_not_available`
- `translation_not_available`
- `asset_fetch_failed`

### Replace markdown line tools with `get_paper_content_window`

Tool:

```python
def get_paper_content_window(
    paper_id: str,
    content_type: Literal["source", "translation"],
    lang: str | None = None,
    mode: Literal["range", "head", "tail", "head_tail", "around"] = "head",
    start_line: int | None = None,
    end_line: int | None = None,
    line_count: int = 80,
    head_lines: int = 40,
    tail_lines: int = 40,
    center_line: int | None = None,
    before_lines: int = 40,
    after_lines: int = 40,
) -> dict[str, Any]
```

Modes:

- `range`: requires `start_line` and `end_line`, inclusive, 1-based.
- `head`: returns lines `1..line_count`.
- `tail`: returns the last `line_count` lines.
- `head_tail`: returns the first `head_lines` plus last `tail_lines`; overlapping or touching ranges are merged.
- `around`: requires `center_line`; `center_line` must be within `1..total_lines`; returns the inclusive range `center_line - before_lines` through `center_line + after_lines`, then clamps only those computed bounds to document boundaries. `before_lines=0` and `after_lines=0` returns only `center_line`.

Return shape:

```json
{
  "paper_id": "p1",
  "content_type": "source",
  "lang": null,
  "mode": "head_tail",
  "total_lines": 1200,
  "ranges": [
    {"start_line": 1, "end_line": 40, "content": "...", "truncated_by_chars": false},
    {"start_line": 1161, "end_line": 1200, "content": "...", "truncated_by_chars": false}
  ],
  "truncated": true,
  "truncated_by_chars": false
}
```

Definitions and limits:

- `truncated=true` means the returned line ranges do not cover the whole document.
- `truncated_by_chars=true` means at least one returned range was shortened by character budget even though the line range was selected.
- Empty markdown is an error because line windows are undefined without at least one line.
- All line arguments must be actual integers where required; booleans, strings, and floats are invalid.
- Count arguments must be positive. `before_lines` and `after_lines` may be zero but not negative.
- `range` rejects invalid or oversized ranges; it must not silently clip explicit user ranges. `start_line` and `end_line` must both be within `1..total_lines` and `start_line <= end_line`.
- Recommended server budgets:
  - `max_window_lines_per_range = 500`
  - `max_window_total_lines = 800`
  - `max_chars_per_range = 12_000`
  - `max_chars_total = 24_000`
- Any selected window exceeding line budgets is rejected with `window_too_large`.
- A selected window inside line budgets but exceeding character budgets is returned with content truncated at the relevant character budget and `truncated_by_chars=true`.
- Character budgets are measured with Python string length after decoding, not UTF-8 bytes or grapheme clusters; truncated markdown is not guaranteed to preserve block integrity.
- If `head_tail` ranges merge, `mode` remains `head_tail`; `truncated=false` only when the merged returned range covers the full document.
- Mode-specific irrelevant parameters are rejected rather than ignored. For example, `mode="head"` with `start_line` or `center_line` is invalid. This catches malformed agent calls early.

Implementation note: put line-window calculation in `mcp_content.py` as a pure helper so tests can cover it black-box without constructing MCP requests. The helper contract should be described by module path, function name, parameter types, return type, and public behavior only; tests should not depend on internal branches or regex details.

### Summary key workflow

Keep key discovery as the default path.

Tool:

```python
def get_paper_summary_keys(
    paper_id: str,
    template: str | None = None,
    max_depth: int = 2,
    include_preview: bool = False,
    max_paths: int = 200,
) -> dict[str, Any]
```

Behavior:

- returns ordered key paths
- supports object fields and array indexes
- optionally includes short previews within a total preview budget
- never returns the full summary body
- `max_depth` must be an actual integer in `0..4`; booleans, strings, and floats are invalid
- `include_preview` must be an actual boolean
- `max_paths` must be an actual positive integer with a default of `200` and hard ceiling of `500`
- preview output has a total character budget, recommended `4_000` chars
- key path strings have a per-path budget, recommended `512` characters, and a total key-path budget, recommended `16_000` characters
- when output is capped, return `truncated=true` plus enough counts for the client to refine its request

Rename `get_paper_summary_key` to `get_paper_summary_value`.

Tool:

```python
def get_paper_summary_value(
    paper_id: str,
    key: str,
    template: str | None = None,
    max_chars: int = 4_000,
    include_subtree: bool = False,
) -> dict[str, Any]
```

Path grammar:

- `field`
- `field.child`
- `array[0]`
- `sections[2].findings[0]`

The root selector `$` is intentionally not implemented in this change, because it reintroduces whole-summary reads. A future `$` design would need strict budgets and a clear warning that it is not the recommended workflow.

Return shape for a scalar/string value:

```json
{
  "paper_id": "p1",
  "template": "deep_read",
  "key": "contributions[0]",
  "value_type": "string",
  "content_format": "text/plain",
  "content": "...",
  "truncated": false
}
```

Return shape for an object/array with default `include_subtree=false`:

```json
{
  "paper_id": "p1",
  "template": "deep_read",
  "key": "sections[0]",
  "value_type": "object",
  "content_format": null,
  "content": null,
  "child_keys": ["title", "summary", "findings"],
  "child_count": 3,
  "returned_child_keys": 3,
  "children_truncated": false,
  "truncated": false
}
```

Rules:

- `max_chars` must be an actual positive integer, default `4_000`, hard ceiling `16_000`.
- `include_subtree` must be an actual boolean.
- For strings, return text subject to `max_chars`.
- For numbers, booleans, and null, return compact scalar text.
- For object/array values, default behavior returns metadata only, not compact JSON for the whole subtree.
- Default object/array metadata is also budgeted: child key count default/hard ceiling `100/300`, per-child-key budget `256` characters, and total child-key budget `8_000` characters. Return `child_count`, `returned_child_keys`, and `children_truncated`.
- If `include_subtree=true`, return compact JSON only for the selected subtree and still apply `max_chars`; do not include unrelated siblings.
- If compact JSON is truncated, set `content_format="text/plain"`, `content_is_valid_json=false`, and `truncated=true` so clients do not parse partial JSON as valid JSON. If not truncated, use `content_format="application/json"` and `content_is_valid_json=true`.

### Follow-up: summary string windows

`get_paper_summary_window` is useful for long generated summary fields, but it is not part of the core change. If added later, it should:

- resolve the summary key first
- accept only string values
- apply the same line-window helper as markdown content
- reject object/array values with `summary_value_not_text`
- use the same line and character budgets as content windows

## Remove old large-content MCP registrations

Stop registering these tools:

- `get_paper_summary`
- `get_paper_summary_key`
- `get_paper_source`
- `get_paper_source_outline`
- `get_paper_source_lines`
- `get_paper_translation_outline`
- `get_paper_translation_lines`

Replace with:

- `get_paper_summary_keys`
- `get_paper_summary_value`
- `get_paper_content_outline`
- `get_paper_content_window`

Stop registering these full-content resources and resource templates:

- `paper://{paper_id}/summary`
- `paper://{paper_id}/summary/{template}`
- `paper://{paper_id}/source`
- `paper://{paper_id}/translation/{lang}`

The underlying helper functions may remain if useful for tests or internal reuse. The implementation should remove `@mcp.tool()` and `@mcp.resource()` public registrations, not blindly delete reusable loader functions. The compatibility decision applies to the MCP public protocol surface.

## Split MCP Auth Entrypoints

### Desired paths

When `mcp_auth_mode="static"`, expose:

```text
/mcp       static bearer, streamable HTTP
/mcp-sse   static bearer, SSE
```

When `mcp_auth_mode="github-oauth"`, target four visible entrypoints:

```text
/mcp             static bearer, streamable HTTP
/mcp-sse         static bearer, SSE
/oauth/mcp       GitHub OAuth, streamable HTTP
/oauth/mcp-sse   GitHub OAuth, SSE, gated by spike before final implementation
```

Static bearer remains required in OAuth mode because ordinary agent CLIs still need a simple non-browser path.

### Canonical OAuth resource and metadata

Canonical GitHub OAuth MCP resource:

```text
{MCP_PUBLIC_BASE_URL}/oauth/mcp
```

The OAuth protected-resource metadata and challenges must reference this resource, not `{base}/mcp`.

Expected public behavior:

- Missing/invalid bearer on `/mcp` returns a static bearer challenge and must not redirect to GitHub.
- Missing/invalid bearer on `/mcp-sse` returns a static bearer challenge and must not redirect to GitHub.
- Missing OAuth credentials on `/oauth/mcp` returns an OAuth challenge whose protected resource is `{base}/oauth/mcp`.
- Static bearer tokens are rejected on `/oauth/mcp` and `/oauth/mcp-sse`.
- OAuth access tokens are rejected on `/mcp` and `/mcp-sse` unless they also exactly equal the configured static token, which should be treated as static-token authentication rather than OAuth authentication.
- Do not pass the configured static access token into the OAuth provider's token verifier in split mode.

Route matrix in OAuth mode:

```text
/mcp                                      static bearer streamable HTTP
/mcp/                                     same behavior, no 307/308 redirect
/mcp-sse                                  static bearer SSE
/mcp-sse/                                 same behavior, no 307/308 redirect
/oauth/mcp                                OAuth streamable HTTP
/oauth/mcp/                               same behavior, no 307/308 redirect
/.well-known/oauth-protected-resource/oauth/mcp
                                          protected-resource metadata for {base}/oauth/mcp
/.well-known/oauth-authorization-server   OAuth authorization-server metadata
/register                                 dynamic client registration, if enabled by current OAuth provider
/auth/callback                            GitHub OAuth callback used by the current provider, unless the spike proves a deliberate adapter to another public callback path
```

If OAuth SSE is proven feasible, also expose:

```text
/oauth/mcp-sse                            OAuth SSE
/oauth/mcp-sse/                           same behavior, no 307/308 redirect
/.well-known/oauth-protected-resource/oauth/mcp-sse
                                          protected-resource metadata for {base}/oauth/mcp-sse, only if provider requires separate resource metadata
```

The implementation must not use a catch-all `Mount("")` for OAuth MCP traffic in split mode. Only exact OAuth protocol/discovery routes needed by FastMCP/GitHub OAuth should be bridged at root.

### FastMCP mount shape

Keep the current lifecycle shape: `create_mcp_apps(config)` should continue returning `(apps, lifespan)` or an equivalent tuple consumed by `api.py`. The `apps` dictionary should use explicit keys:

```python
{
  "bearer-streamable-http": app,
  "bearer-sse": app,
  "oauth-streamable-http": app,  # only in github-oauth mode
  "oauth-sse": app,              # only after OAuth SSE spike passes
}
```

Static bearer external Starlette mounts should be explicit. OAuth transport exposure should use an exact-path bridge/router rather than a catch-all root mount:

```python
Mount("/mcp", app=mcp_apps["bearer-streamable-http"])
Mount("/mcp-sse", app=mcp_apps["bearer-sse"])
# OAuth apps are exposed by exact protocol/MCP routes, not Mount("").
```

For OAuth streamable HTTP, the preferred implementation is: create the FastMCP OAuth app with internal `path="/oauth/mcp"` so FastMCP generates protected-resource metadata for `/oauth/mcp`, then expose only the exact MCP and OAuth protocol paths through a small ASGI bridge/router. Do not rely on `MCP_PUBLIC_BASE_URL` containing a path; it remains an origin only.

The bridge must forward the original path expected by the FastMCP OAuth app and must only match the inventory of OAuth routes confirmed by the spike. It must not be a catch-all `Mount("")`. If the spike proves another implementation shape is simpler and still satisfies all public route tests, that implementation is acceptable, but the route inventory and tests are mandatory.

Current code uses one module-global `FastMCP` instance and mutates its auth while creating apps. Split auth must start with a spike:

1. record the exact FastMCP route inventory for static streamable, static SSE, OAuth streamable, and attempted OAuth SSE: external path, internal FastMCP path, auth challenge, protected-resource metadata, SSE message POST path, and lifecycle needs;
2. prove two streamable apps with different auth providers can safely coexist on the global FastMCP instance, or
3. refactor to a FastMCP server factory, e.g. `create_snapshot_mcp_server(auth=...)` plus `register_snapshot_tools(server)`.

This spike is an implementation precondition, not an optional nice-to-have. The implementation should prefer the factory if the spike shows any auth leakage, duplicate route registration, or metadata confusion.

### OAuth SSE feasibility gate

The target is four entrypoints, including OAuth SSE. However, OAuth SSE should not be implemented by guesswork.

Before marking the feature complete, prove all of the following through public behavior:

- `/oauth/mcp-sse` GET produces the expected SSE handshake/challenge behavior.
- the associated SSE message POST endpoint is protected under the OAuth prefix and does not leak to `/messages` or static bearer paths. Static SSE must also have its message POST endpoint under the static prefix and protected by static bearer auth.
- protected-resource metadata and `WWW-Authenticate` reference the OAuth SSE resource if the provider treats SSE as a separate resource.
- at least one MCP client/test harness can complete the OAuth SSE flow, or the project explicitly documents why OAuth SSE is unsupported.

If this gate fails, stop and ask for user approval before shipping a three-entrypoint variant. Do not silently compromise static bearer `/mcp-sse`.

## Recommended Agent Workflows

### Read paper source or translation

1. `search_papers` or `filter_papers`.
2. `get_paper_metadata`.
3. `get_paper_content_outline` to discover section ranges.
4. `get_paper_content_window` with `range` or `around` to fetch only relevant lines.
5. Use `head_tail` only for quick orientation.

### Read summary

1. `get_paper_metadata` to discover templates.
2. `get_paper_summary_keys` to discover structure.
3. `get_paper_summary_value` for the specific key.
4. Core scope only supports bounded prefix reads for long summary string values. Reading the middle or tail of a long summary string requires the follow-up `get_paper_summary_window` tool.

### Auth selection

- CLI agents should use `/mcp` or `/mcp-sse` with `Authorization: Bearer <token>`.
- Browser/OAuth-capable clients should use `/oauth/mcp`, and `/oauth/mcp-sse` only after the OAuth SSE feasibility gate passes.

## Error Handling

Use structured `McpToolError` consistently.

New or reused error codes:

- `invalid_content_type`
- `missing_lang`
- `invalid_lang_for_source`
- `invalid_lang`
- `invalid_window_mode`
- `invalid_line_range`
- `invalid_line_count`
- `invalid_section_count`
- `window_too_large`
- `source_not_available`
- `translation_not_available`
- `template_not_available`
- `invalid_summary_key`
- `summary_key_not_found`
- `summary_value_not_text`
- `asset_fetch_failed`

Tool errors should include the user-supplied identifiers where safe: `paper_id`, `content_type`, `lang`, `mode`, `key`, and line parameters.

## Testing Strategy

Follow the project black-box testing policy. Tests should assert only public input/output behavior.

### Helper tests

For public helper contracts in `mcp_content.py`, define tests by module path, function name, parameter types, return types, and expected behavior only.

Line-window helper behavior to cover:

- `head` clamps to document length within budgets
- `tail` returns final lines within budgets
- `head_tail` merges overlap
- `range` rejects invalid start/end
- `range` rejects oversized ranges with `window_too_large`
- `around` rejects center outside `1..total_lines`
- `around` clamps computed bounds at document boundaries
- bool, string, float, zero, negative, and excessive counts are rejected according to parameter rules
- a huge single-line range returns `truncated_by_chars=true` within character budgets

Summary helper behavior to cover:

- key discovery respects `max_depth`, `max_paths`, preview flag, and preview budget
- bool/string/float depth and path limits are rejected
- value reads return only selected scalar/string content
- object/array reads default to metadata and child keys, not full subtree content
- `include_subtree=true` is capped by `max_chars` and does not include unrelated siblings
- `$` root selector is rejected in this change

### MCP protocol tests

For MCP behavior, use public MCP protocol requests only:

- `tools/list` includes `get_paper_content_outline`, `get_paper_content_window`, `get_paper_summary_keys`, and `get_paper_summary_value`
- `tools/list` omits removed full-content tool names
- calling a removed tool name returns public unknown-tool behavior
- `resources/list` does not advertise full-content paper summary/source/translation resources
- `resources/templates/list` does not advertise full-content paper summary/source/translation templates
- `get_paper_metadata` includes `has_source`, `available_translations`, `available_summary_templates`, and `has_bibtex`
- `get_paper_content_outline(source)` rejects non-null `lang`
- `get_paper_content_outline(translation)` requires valid language
- `get_paper_content_window` returns expected metadata and ranges for each mode
- unavailable source/translation returns the expected error code
- `get_paper_summary_value` reads a specific key and does not return unrelated sibling content

### Transport tests

For `api.py` / MCP mounting:

- static mode exposes `/mcp` and `/mcp-sse`
- static mode does not expose `/oauth/mcp`, `/oauth/mcp-sse`, or OAuth well-known/DCR routes
- OAuth mode exposes static bearer `/mcp` and `/mcp-sse`
- OAuth mode exposes `/oauth/mcp`
- OAuth mode exposes `/oauth/mcp-sse` only after the feasibility gate passes
- static bearer endpoints challenge missing/invalid bearer and accept valid bearer
- OAuth endpoints preserve GitHub OAuth challenge/metadata behavior for `{base}/oauth/mcp`
- old OAuth protected-resource metadata for `{base}/mcp` is absent or no longer advertised in split mode
- static bearer paths do not redirect to GitHub in OAuth mode
- static bearer token is rejected on OAuth endpoints
- OAuth access token is rejected on static endpoints unless it exactly equals the configured static token and is treated as static auth
- no MCP route variant returns 307/308 solely due to trailing slash
- public API/admin/advanced/unknown routes keep their expected behavior and are not answered by OAuth/FastMCP responses
- metadata, DCR, callback, and well-known routes do not override unrelated HTTP API routes
- static SSE GET and message POST are both verified under the static prefix
- if OAuth SSE is attempted, SSE GET and message POST are both verified under the OAuth prefix

## Documentation Strategy

Update MCP docs in both languages with parity:

- `docs/en/api-and-mcp.md`
- `docs/zh/api-and-mcp.md`

Root `README.md` and `README_ZH.md` must be checked; update them if their MCP endpoint/auth overview becomes inaccurate. OpenSpec or archived MCP specs should also be checked and updated or clearly marked archived if they still describe old tools/resources.

Docs should:

- describe the recommended key/window workflow
- document the static and OAuth MCP endpoints and when each is available
- document whether OAuth SSE passed the feasibility gate
- make clear that full-content reads are intentionally not part of the MCP public surface
- document `has_source`, `available_translations`, `available_summary_templates`, and `has_bibtex`
- provide concrete examples for:
  - reading first 80 lines of source
  - reading around a section line
  - reading a translation tail
  - discovering summary keys then reading one value
  - CLI bearer setup
  - OAuth client setup

## Migration / Compatibility

No database or static export migration is needed.

MCP client compatibility is intentionally not guaranteed for old tool names, resource URIs, or route assumptions. This is acceptable because the MCP API is not yet treated as a stable external contract. The rest of the HTTP API remains unchanged.

## Open Risks

1. FastMCP may not support OAuth SSE under a non-root OAuth prefix exactly as desired. Mitigation: test `/oauth/mcp-sse` early; stop for approval if the four-entrypoint target is impossible without compromising static bearer SSE.
2. FastMCP OAuth metadata may assume a single canonical resource path. Mitigation: canonicalize OAuth as `{base}/oauth/mcp` and prove discovery/challenge payloads reference that resource.
3. The global FastMCP instance may leak auth or metadata between bearer and OAuth apps. Mitigation: spike first, then refactor to a per-server factory if needed.
4. Removing full-content resources may surprise manual users. Mitigation: document window/key workflows clearly and provide `head_tail` for orientation.
5. Character budgets may truncate a long single line. Mitigation: expose `truncated_by_chars` and keep returned payload bounded.

## Recommendation

Implement the clean break now: remove old large-content MCP tools and resources, add unified content outline/window tools, rename summary keyed reads to `get_paper_summary_value`, and split static bearer/OAuth into separate visible MCP entrypoints with canonical OAuth resource `{base}/oauth/mcp`. Start implementation with the FastMCP split-auth route-inventory and OAuth SSE spike so the final plan is grounded in proven transport behavior rather than assumptions.
