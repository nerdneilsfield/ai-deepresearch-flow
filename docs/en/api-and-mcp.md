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

The project exposes MCP tools for AI agent access via FastMCP. The recommended public surface is tool-based and bounded: discover metadata, discover outlines or summary keys, then read specific line windows or specific summary values.

**Endpoints:**

- Static bearer Streamable HTTP: `http://<host>:8001/mcp`
- Static bearer SSE: `http://<host>:8001/mcp-sse`
- GitHub OAuth Streamable HTTP: `https://<public-host>/oauth/mcp`
- OAuth SSE: currently absent/unsupported. Do not configure `/oauth/mcp-sse` unless a future gate explicitly adds it.
- OAuth discovery/protocol routes: `/.well-known/`, `/.well-known/oauth-protected-resource/oauth/mcp`, `/authorize`, `/token`, `/register`, `/auth/callback`, `/consent`
- Optional protocol header: `mcp-protocol-version` (`2025-03-26` or `2025-06-18`)

**Static reads priority:** `PAPER_DB_STATIC_EXPORT_DIR` → `PAPER_DB_STATIC_BASE` / `PAPER_DB_STATIC_BASE_URL`

Full-content MCP reads and full-content paper URI resources are old/removed/archived public-surface patterns. Do not recommend `get_paper_summary`, `get_paper_source`, source/translation line-specific legacy tools, or full-content `paper://...` resources for agent workflows; use the bounded tools below instead. The small `paper://{paper_id}/metadata` resource may remain for compatibility, but agents should prefer `get_paper_metadata`.

Endpoint behavior by deployment mode:

| Endpoint | Static bearer mode | GitHub OAuth mode |
| --- | --- | --- |
| `/mcp` | Streamable HTTP with `Authorization: Bearer <MCP_ACCESS_TOKEN>` | Still static bearer only |
| `/mcp-sse` | SSE with `Authorization: Bearer <MCP_ACCESS_TOKEN>` | Still static bearer only |
| `/oauth/mcp` | Not used | Streamable HTTP with GitHub OAuth |
| `/oauth/mcp-sse` | Not used | Unsupported/absent for now |
| OAuth protocol routes | Not used | Required for discovery, registration, authorization, token exchange, callback, and consent |

Use static bearer for ordinary CLI agents and automation. Use GitHub OAuth for hosted clients such as ChatGPT/Claude that need an interactive OAuth flow. Do not send the static bearer token to `/oauth/mcp`; that endpoint deliberately rejects it.

### Advanced Search Browser Authentication

`SEARCH_AUTH_MODE` controls authentication for `/api/v1/search/advanced` independently of
`MCP_AUTH_MODE`:

- `static`: accept `SEARCH_ACCESS_TOKEN` only (default).
- `github-oauth`: accept the seven-day browser session only.
- `both`: accept either mechanism.

GitHub browser login reuses `MCP_PUBLIC_BASE_URL`, `GITHUB_OAUTH_CLIENT_ID`,
`GITHUB_OAUTH_CLIENT_SECRET`, and `MCP_GITHUB_ALLOWED_USER_IDS`. Its callback is
`/auth/callback/web`; session inspection and logout use `/api/v1/auth/session` and
`POST /api/v1/auth/logout`. The GitHub access token is used only to read the user's public numeric
ID and login, then discarded.

### Auth Modes

#### Static Bearer

Use `--mcp-access-token` or `MCP_ACCESS_TOKEN`; send `Authorization: Bearer <token>`. Applies to both `/mcp` and `/mcp-sse`.

CLI example:

```bash
MCP_ACCESS_TOKEN=your-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db papers.db \
  --mcp-access-token "$MCP_ACCESS_TOKEN"
```

Client URL: `https://<public-host>/mcp` for Streamable HTTP, or `https://<public-host>/mcp-sse` for SSE.

#### GitHub OAuth

Set `MCP_AUTH_MODE=github-oauth`. OAuth clients use Streamable HTTP at `/oauth/mcp`; static bearer tokens are rejected there. `/mcp` and `/mcp-sse` remain static bearer endpoints.

Required environment variables:

- `MCP_AUTH_MODE=github-oauth`
- `MCP_PUBLIC_BASE_URL` (origin only, for example `https://papers.example.com`; do not include `/oauth/mcp`)
- `GITHUB_OAUTH_CLIENT_ID`
- `GITHUB_OAUTH_CLIENT_SECRET`
- `MCP_GITHUB_ALLOWED_USER_IDS` (numeric GitHub user IDs, required)
- `MCP_ACCESS_TOKEN` (still required for static bearer `/mcp` and `/mcp-sse`)

Recommended additional setting:

- `MCP_OAUTH_CLIENT_CACHE=/path/to/mcp-oauth-clients.json`: persists the Dynamic Client Registration client registry. Pinning this JSON file path keeps registered OAuth clients usable across restarts. If the cache is still lost and ChatGPT/Claude sends an old but syntactically valid dynamic `client_id` to `/authorize`, drflow attempts a reauth recovery flow instead of issuing a token directly; malformed client IDs still fail closed. Treat repeated recovery logs as a signal that this cache path is not mounted persistently.

OAuth client setup summary:

1. Configure the MCP client URL as `https://<public-host>/oauth/mcp`.
2. Let the client discover protected-resource metadata at `https://<public-host>/.well-known/oauth-protected-resource/oauth/mcp`.
3. Complete GitHub OAuth through the server routes: `/authorize`, `/token`, `/register`, `/auth/callback`, `/consent`.
4. Do not use `/oauth/mcp-sse`; the OAuth SSE gate currently makes it absent/unsupported.

> **Note:** MCP, advanced-search, and admin bearer tokens remain separate. MCP OAuth and browser advanced-search OAuth deliberately share the configured GitHub OAuth App credentials and user-ID allowlist.

##### Create the GitHub OAuth App

Create a **GitHub OAuth App**, not a GitHub App. The GitHub OAuth App is only the upstream identity provider; drflow remains the MCP authorization/resource server for ChatGPT, Claude, and other MCP clients.

1. Open GitHub while signed in as the account or organization that should own the OAuth app.
2. Go to **Settings → Developer settings → OAuth Apps**.
   - Personal account: click your avatar → **Settings** → **Developer settings** → **OAuth Apps**.
   - Organization-owned app: open the organization → **Settings** → **Developer settings** → **OAuth Apps**.
3. Click **New OAuth App**.
4. Fill in:

   | GitHub field | Value |
   | --- | --- |
   | Application name | Any clear name, for example `drflow-registration-mcp` |
   | Homepage URL | `https://<public-host>` |
   | Application description | Optional |
   | Authorization callback URL | `https://<public-host>/auth/callback` |

   For example, if `MCP_PUBLIC_BASE_URL=https://drflow.example.com`, use:

   ```text
   Homepage URL: https://drflow.example.com
   Authorization callback URL: https://drflow.example.com/auth/callback
   ```

   Do **not** use `/oauth/mcp` as the GitHub callback URL. `/oauth/mcp` is the MCP client endpoint; `/auth/callback` is where GitHub redirects the user's browser after login.

5. Click **Register application**.
6. Copy the **Client ID** into `GITHUB_OAUTH_CLIENT_ID`.
7. Click **Generate a new client secret**, copy it once, and store it in `GITHUB_OAUTH_CLIENT_SECRET`. Treat it as a secret; do not commit it.
8. Find the numeric GitHub user IDs allowed to access MCP and put them in `MCP_GITHUB_ALLOWED_USER_IDS`.

   This value is the stable numeric `id` field returned by the GitHub API. It is **not** the GitHub username, display name, email address, or OAuth client ID. If this variable is empty or contains a username, drflow refuses to start with:

   ```text
   MCP_GITHUB_ALLOWED_USER_IDS must contain numeric GitHub user IDs
   ```

   For your currently authenticated GitHub CLI user:

   ```bash
   gh api user --jq .id
   ```

   For a specific username:

   ```bash
   gh api users/<github-username> --jq .id
   # or:
   curl -s https://api.github.com/users/<github-username> | jq -r .id
   ```

   Example:

   ```bash
   gh api users/octocat --jq .id
   ```

   Multiple users are comma-separated:

   ```env
   MCP_GITHUB_ALLOWED_USER_IDS=12345678,87654321
   ```

   Do not quote the list and do not add spaces:

   ```env
   # Correct:
   MCP_GITHUB_ALLOWED_USER_IDS=12345678,87654321

   # Incorrect:
   MCP_GITHUB_ALLOWED_USER_IDS=alice,bob
   MCP_GITHUB_ALLOWED_USER_IDS="12345678, 87654321"
   ```

9. Configure drflow:

   ```env
   MCP_AUTH_MODE=github-oauth
   MCP_PUBLIC_BASE_URL=https://<public-host>
  GITHUB_OAUTH_CLIENT_ID=...
  GITHUB_OAUTH_CLIENT_SECRET=...
  MCP_GITHUB_ALLOWED_USER_IDS=12345678
  MCP_OAUTH_CLIENT_CACHE=/data/mcp-oauth-clients.json

  # Still required for static-bearer /mcp and /mcp-sse:
  MCP_ACCESS_TOKEN=...
  ```

   The equivalent CLI option is `--mcp-oauth-client-cache /data/mcp-oauth-clients.json`. This file is only for the MCP OAuth client-registration cache; put it on a persistent container volume and treat it like config, not something to commit.

10. Ensure the reverse proxy forwards all OAuth routes to drflow:

    - `/oauth/mcp`
    - `/.well-known/`
    - `/authorize`
    - `/token`
    - `/register`
    - `/auth/callback`
    - `/consent`

11. Configure MCP clients:

    - ChatGPT/Claude OAuth endpoint: `https://<public-host>/oauth/mcp`
    - Static-bearer CLI endpoint: `https://<public-host>/mcp` with `Authorization: Bearer <MCP_ACCESS_TOKEN>`
    - SSE static-bearer endpoint, when needed: `https://<public-host>/mcp-sse`

If GitHub returns a callback mismatch error, check the callback URL in the GitHub OAuth App first. It must exactly match the externally reachable drflow callback URL, including scheme, host, and path. If you deploy staging and production under different public hosts, create separate GitHub OAuth Apps so each environment has its own callback URL and client secret.

Operational notes:

- `MCP_PUBLIC_BASE_URL` must be the externally reachable HTTPS origin only. Do not append `/mcp` or `/oauth/mcp`.
- `MCP_GITHUB_ALLOWED_USER_IDS` uses stable numeric GitHub user IDs, not display names. This keeps drflow single-library/single-tenant while allowing only specific GitHub identities to access MCP.
- The GitHub OAuth app callback must match the server's OAuth callback route exposed by this deployment, currently `/auth/callback` behind the same public base URL.
- If a client keeps a stale `Authorization: Bearer ...` header while testing OAuth, remove that header before starting the OAuth flow.

### Recommended Agent Workflow

1. Search with `search_papers`, `search_papers_by_keyword`, or `search_papers_semantic`.
2. Call `get_paper_metadata` to discover availability fields.
3. For source or translation markdown, call `get_paper_content_outline`, then `get_paper_content_window`.
4. For summaries, call `get_paper_summary_keys`, then `get_paper_summary_value` for the exact key.

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

Get paper metadata and availability flags. Call this before reading source, translations, summaries, or BibTeX.

Returns: `paper_id`, `title`, `year`, `venue`, `doi`, `arxiv_id`, `openreview_id`, `paper_pw_url`, `preferred_summary_template`, `has_source`, `available_translations`, `available_summary_templates`, `has_bibtex`.

Availability fields:

- `has_source`: source markdown can be read with content window tools.
- `available_translations`: normalized language tags accepted by content tools when `content_type="translation"`.
- `available_summary_templates`: summary template tags accepted by summary tools.
- `has_bibtex`: BibTeX is available through `get_paper_bibtex`.
</details>

<details>
<summary><code>get_paper_bibtex(paper_id) → dict</code></summary>

Get persisted BibTeX payload for a paper. Returns canonical DOI from paper metadata and BibTeX entry text when available.

Returns: `paper_id`, `doi`, `bibtex_raw`, `bibtex_key`, `entry_type`.
</details>

<details>
<summary><code>get_paper_content_outline(paper_id, content_type, lang=None, max_sections=200) → dict</code></summary>

Get a bounded heading outline for source or translated markdown.

- `content_type`: `"source"` or `"translation"`
- `lang`: required for translations, omitted for source; use tags from `available_translations`
- `max_sections`: default `200`, hard-capped by the server

Returns: `paper_id`, `content_type`, `lang`, `total_lines`, `sections` with heading levels and line ranges.
</details>

<details>
<summary><code>get_paper_content_window(..., mode="head", line_count=80, ...) → dict</code></summary>

Get a bounded line window for source or translated markdown.

- Common arguments: `paper_id`, `content_type`, optional `lang`
- Modes: `head`, `tail`, `head_tail`, `around`, `range`
- `head`: first `line_count` lines
- `tail`: last `line_count` lines
- `head_tail`: first `head_lines` plus last `tail_lines`
- `around`: `center_line` with `before_lines` and `after_lines`
- `range`: inclusive `start_line` to `end_line`

Returns: `paper_id`, `content_type`, `lang`, line bounds, `total_lines`, `content`, and truncation/window metadata.

Line numbers are 1-based and inclusive. Explicit ranges must be valid; use `outline` first when you need section boundaries. Responses are bounded by server line/character caps and include truncation metadata when the requested window is reduced.
</details>

<details>
<summary><code>get_paper_summary_keys(paper_id, template=None, max_depth=2, include_preview=False, max_paths=200) → dict</code></summary>

Get recursive summary key paths in document order.

- `template`: uses the preferred template when omitted
- `max_depth`: maximum nesting depth (default `2`)
- `include_preview`: include bounded value previews when `True`
- `max_paths`: maximum paths returned

Returns: `paper_id`, `template`, `paths`, `total_paths`, `returned_paths`, `truncated`.

Keys use JSON-style dot/index paths such as `contribution.main` or `experiments[0].result`. Discover keys first instead of downloading the entire summary JSON.
</details>

<details>
<summary><code>get_paper_summary_value(paper_id, key, template=None, max_chars=4000, include_subtree=False) → dict</code></summary>

Get one addressed summary value. For object or array nodes, the default returns child-key metadata instead of a large subtree; set `include_subtree=True` only when needed.

Returns: `paper_id`, `template`, `key`, `value_type`, `content_format`, `content`, child-key metadata when applicable, and `truncated`.

Long summary strings are prefix-bounded by `max_chars`. Middle/tail summary reads require the future `get_paper_summary_window` tool.
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

### Examples

Read first 80 lines of source:

```json
{"tool":"get_paper_content_window","arguments":{"paper_id":"paper-123","content_type":"source","mode":"head","line_count":80}}
```

Find sections, then read around a section line:

```json
{"tool":"get_paper_content_outline","arguments":{"paper_id":"paper-123","content_type":"source"}}
{"tool":"get_paper_content_window","arguments":{"paper_id":"paper-123","content_type":"source","mode":"around","center_line":245,"before_lines":30,"after_lines":50}}
```

Read the tail of a Chinese translation:

```json
{"tool":"get_paper_content_window","arguments":{"paper_id":"paper-123","content_type":"translation","lang":"zh","mode":"tail","line_count":80}}
```

Discover summary keys, then read one value:

```json
{"tool":"get_paper_summary_keys","arguments":{"paper_id":"paper-123","template":"deep_read","max_depth":3,"include_preview":true}}
{"tool":"get_paper_summary_value","arguments":{"paper_id":"paper-123","template":"deep_read","key":"experiments.main_result","max_chars":4000}}
```

### Archived MCP resources

`paper://{paper_id}/summary`, `paper://{paper_id}/summary/{template}`, `paper://{paper_id}/source`, and `paper://{paper_id}/translation/{lang}` are historical full-content resource patterns. They are not the recommended MCP public surface for current agents. Prefer bounded tools: `get_paper_content_outline`, `get_paper_content_window`, `get_paper_summary_keys`, and `get_paper_summary_value`.
