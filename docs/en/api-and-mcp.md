[← Back to README](../../README.md)

# API & MCP

## Admin API

Enable the admin API to add or delete papers remotely via Bearer token authentication.

```bash
PAPER_DB_ADMIN_TOKEN=your-secret-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

Pass token via CLI: `--admin-token your-secret-token`

### Endpoints

All endpoints require `Authorization: Bearer <token>`.

#### `POST /api/v1/admin/papers`

Batch add papers (up to 200 per request).

Response: `{ added, skipped, errors, paper_ids }`

```bash
curl -X POST https://api.example.com/api/v1/admin/papers \
  -H "Authorization: Bearer your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"papers": [{"paper_title": "...", "paper_authors": [...], ...}]}'
```

#### `DELETE /api/v1/admin/papers/{paper_id}`

Delete a paper and all its relations.

Response: `{ deleted: true, paper_id }`

```bash
curl -X DELETE https://api.example.com/api/v1/admin/papers/{paper_id} \
  -H "Authorization: Bearer your-secret-token"
```

---

## Push from Local DB to Remote

Use `api push` to merge a locally-built snapshot DB into a remote deployment.

### Configuration

```toml
# remote.toml
[remote]
api_base_url = "https://api.example.com"
admin_token = "env:PAPER_DB_ADMIN_TOKEN"
batch_size = 10

[remote.semantic]
max_rows = 25
max_payload_bytes = 4000000
timeout = 120
retries = 3
retry_backoff_seconds = 2

[remote.storage]
type = "webdav"
url = "https://cdn.example.com/paper-static"
username = "deploy"
password = "env:PAPER_DB_WEBDAV_PASSWORD"
```

### Commands

```bash
# Preview
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --dry-run

# Push to remote
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml

# Push metadata + semantic LanceDB chunks
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --embed-db ./dist/paper_vectors \
  --config remote.toml

# Only API metadata
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --only-api

# Only static storage assets
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --only-storage --storage-concurrency 8

# Retry failed static files
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml \
  --retry-failed push-static-errors.json

# Slice by paper index
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --embed-db ./dist/paper_vectors \
  --config remote.toml --only-api \
  --start-idx 100 --end-idx 200
```

### Key Notes

- `--static-export-dir` optional — when provided, includes summary JSON for FTS + preview
- `--embed-db` optional — pushes semantic chunks after metadata
- Duplicate papers skipped automatically
- Storage backend: `webdav`
- `push-static-errors.json` / `push-semantic-errors.json` for retry
- `--only-api` and `--only-storage` are mutually exclusive
- `--dry-run` skips semantic push

### Push Semantic Only

```bash
# Push all semantic groups
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml

# Retry failed semantic batches
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --retry-failed push-semantic-errors.json

# By chunk window (0-based, end exclusive, -1 = to end)
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --start-chunk-idx 1000 --end-chunk-idx 2000
```

Notes: Chunk-window auto-expands to full `(doc_id, template_tag)` groups. `--retry-failed` semantic only. Cannot combine with `--start-chunk-idx/--end-chunk-idx`.

---

## MCP (FastMCP Streamable HTTP + SSE)

### Overview

The project exposes MCP tools and resources for AI agent access via FastMCP.

**Endpoints:**

- Streamable HTTP: `http://<host>:8001/mcp`
- SSE: `http://<host>:8001/mcp-sse`
- Optional protocol header: `mcp-protocol-version` (`2025-03-26` or `2025-06-18`)

**Static reads priority:** `PAPER_DB_STATIC_EXPORT_DIR` → `PAPER_DB_STATIC_BASE` / `PAPER_DB_STATIC_BASE_URL`

### Auth Modes

#### Static Bearer

Use `--mcp-access-token` or `MCP_ACCESS_TOKEN`; `Authorization: Bearer <token>`. Applies to both `/mcp` and `/mcp-sse`.

#### GitHub OAuth

Set `MCP_AUTH_MODE=github-oauth`. OAuth only for Streamable HTTP `/mcp`.

Required environment variables:

- `MCP_PUBLIC_BASE_URL`
- `GITHUB_OAUTH_CLIENT_ID`
- `GITHUB_OAUTH_CLIENT_SECRET`
- `MCP_GITHUB_ALLOWED_USER_IDS` (numeric GitHub user IDs, required)
- `MCP_ACCESS_TOKEN`

OAuth routes: `/.well-known/`, `/authorize`, `/token`, `/register`, `/auth/callback`, `/consent`

> **Note:** MCP token, advanced-search token, and admin token are separate credentials.

### MCP Tools

<details>
<summary><code>search_papers(query, limit=10) → list[dict]</code></summary>

Full-text search for papers (relevance-ranked). Use when you only have topic keywords.

Returns: `paper_id`, `title`, `year`, `venue`, `snippet_markdown`.
</details>

<details>
<summary><code>search_papers_by_keyword(keyword, limit=10) → list[dict]</code></summary>

Search papers by keyword/tag (exact match). Use when you know specific keywords or tags.

Returns: `paper_id`, `title`, `year`, `venue`, `snippet_markdown`.
</details>

<details>
<summary><code>search_papers_semantic(query, top_n=10, mmr_lambda=None, rerank="auto", filters=None) → dict</code></summary>

Run the full advanced semantic search pipeline and return its payload. Requires advanced search configuration on the MCP server.

- `mmr_lambda`: MMR diversity parameter (uses server default when `None`)
- `rerank`: Reranking mode (`"auto"` by default)
- `filters`: Optional structured filter parameters
</details>

<details>
<summary><code>get_paper_metadata(paper_id) → dict</code></summary>

Get paper metadata and available summary templates. Call this first before requesting a summary to discover available templates.

Returns: `paper_id`, `title`, `year`, `venue`, `doi`, `arxiv_id`, `openreview_id`, `paper_pw_url`, `preferred_summary_template`, `available_summary_templates`, `has_bibtex`.
</details>

<details>
<summary><code>get_paper_bibtex(paper_id) → dict</code></summary>

Get persisted BibTeX payload for a paper. Returns canonical DOI from paper metadata and BibTeX entry text when available.

Returns: `paper_id`, `doi`, `bibtex_raw`, `bibtex_key`, `entry_type`.
</details>

<details>
<summary><code>get_paper_summary(paper_id, template=None, max_chars=None) → str</code></summary>

Get summary JSON as raw string. Uses preferred template if `template` is not specified. Returns the full JSON content (not a URL).
</details>

<details>
<summary><code>get_paper_summary_keys(paper_id, template=None, max_depth=2, include_preview=False) → dict</code></summary>

Get recursive summary key paths in document order.

- `max_depth`: Maximum nesting depth (default 2)
- `include_preview`: Include value previews when `True`

Returns: `paper_id`, `template`, `keys` (list of key paths with types and optional previews).
</details>

<details>
<summary><code>get_paper_summary_key(paper_id, key, template=None, max_chars=None) → dict</code></summary>

Get a single addressed summary node.

Returns: `paper_id`, `template`, `key`, `value`, `type`.
</details>

<details>
<summary><code>get_paper_source(paper_id, max_chars=None) → str</code></summary>

Get source markdown text. Content may be large; use `max_chars` to limit size.
</details>

<details>
<summary><code>get_paper_source_outline(paper_id) → dict</code></summary>

Get the source markdown outline as section ranges.

Returns: `paper_id`, `sections` (list of `{ heading, level, start_line, end_line }`).
</details>

<details>
<summary><code>get_paper_source_lines(paper_id, start_line, end_line) → dict</code></summary>

Get a 1-based inclusive slice of the source markdown.

Returns: `paper_id`, `start_line`, `end_line`, `content`.
</details>

<details>
<summary><code>get_paper_translation_outline(paper_id, lang) → dict</code></summary>

Get the translated markdown outline as section ranges.

Returns: `paper_id`, `lang`, `sections` (list of `{ heading, level, start_line, end_line }`).
</details>

<details>
<summary><code>get_paper_translation_lines(paper_id, lang, start_line, end_line) → dict</code></summary>

Get a 1-based inclusive slice of the translated markdown.

Returns: `paper_id`, `lang`, `start_line`, `end_line`, `content`.
</details>

<details>
<summary><code>get_database_stats() → dict</code></summary>

Get database statistics. Returns totals, year/month distributions, and top facets (authors, venues, keywords, institutions, tags).
</details>

<details>
<summary><code>list_top_facets(category, limit=20) → list[dict]</code></summary>

List top facet values.

- `category`: One of `author`, `venue`, `keyword`, `institution`, `tag`

Returns: `[{ value, paper_count }]`.
</details>

<details>
<summary><code>filter_papers(author=None, venue=None, year=None, keyword=None, tag=None, limit=10) → list[dict]</code></summary>

Filter papers by structured fields. Use for precise filtering by author, venue, year, keyword, or tag.

Returns: `paper_id`, `title`, `year`, `venue`.
</details>

### MCP Resources (URI Access)

| URI | Description | MIME Type |
|-----|-------------|-----------|
| `paper://{paper_id}/metadata` | Paper metadata including title, authors, year, venue, DOI, and available summary templates | `application/json` |
| `paper://{paper_id}/summary` | Paper summary using the preferred template | `application/json` |
| `paper://{paper_id}/summary/{template}` | Paper summary using a specific template | `application/json` |
| `paper://{paper_id}/source` | Source markdown content of the paper | `text/markdown` |
| `paper://{paper_id}/translation/{lang}` | Translated markdown content in the specified language | `text/markdown` |
