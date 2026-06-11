[← 返回 README](../README_ZH.md)

# API 与 MCP

## Admin API

启用 Admin API 后，可以通过 Bearer token 认证远程添加或删除论文。

```bash
PAPER_DB_ADMIN_TOKEN=your-secret-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

也可以通过命令行传 token：`--admin-token your-secret-token`

### 接口

所有接口均需要 `Authorization: Bearer <token>`。

#### `POST /api/v1/admin/papers`

批量添加论文（单次最多 200 条）。

返回：`{ added, skipped, errors, paper_ids }`

```bash
curl -X POST https://api.example.com/api/v1/admin/papers \
  -H "Authorization: Bearer your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"papers": [{"paper_title": "...", "paper_authors": [...], ...}]}'
```

#### `DELETE /api/v1/admin/papers/{paper_id}`

删除一篇论文及其所有关联数据。

返回：`{ deleted: true, paper_id }`

```bash
curl -X DELETE https://api.example.com/api/v1/admin/papers/{paper_id} \
  -H "Authorization: Bearer your-secret-token"
```

---

## 从本地数据库推送到远程

使用 `api push` 将本地构建的 snapshot 数据库合并到远程部署中。

### 配置

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

### 命令

```bash
# 预览（不实际推送）
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --dry-run

# 推送到远程
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml

# 推送元数据 + semantic LanceDB 分块
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --embed-db ./dist/paper_vectors \
  --config remote.toml

# 仅推送 API 元数据
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --only-api

# 仅推送静态存储资源
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --only-storage --storage-concurrency 8

# 重试失败的静态文件
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml \
  --retry-failed push-static-errors.json

# 按论文索引切片推送
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --embed-db ./dist/paper_vectors \
  --config remote.toml --only-api \
  --start-idx 100 --end-idx 200
```

### 注意事项

- `--static-export-dir` 可选——提供后会包含 summary JSON，用于全文搜索和预览
- `--embed-db` 可选——在元数据之后推送 semantic 分块
- 重复论文会自动跳过
- 存储后端：`webdav`
- 重试文件：`push-static-errors.json` / `push-semantic-errors.json`
- `--only-api` 与 `--only-storage` 互斥
- `--dry-run` 会跳过 semantic 推送

### 仅推送 Semantic

```bash
# 推送所有 semantic 分组
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml

# 重试失败的 semantic 批次
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --retry-failed push-semantic-errors.json

# 按分块窗口推送（0 起始，end 不包含，-1 表示到末尾）
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --start-chunk-idx 1000 --end-chunk-idx 2000
```

说明：分块窗口会自动扩展到完整的 `(doc_id, template_tag)` 分组。`--retry-failed` 仅用于 semantic 重试。不能与 `--start-chunk-idx/--end-chunk-idx` 同时使用。

---

## MCP（FastMCP Streamable HTTP + SSE）

### 概述

项目通过 FastMCP 暴露 MCP 工具和资源，供 AI agent 调用。

**接口地址：**

- Streamable HTTP：`http://<host>:8001/mcp`
- SSE：`http://<host>:8001/mcp-sse`
- 可选协议头：`mcp-protocol-version`（`2025-03-26` 或 `2025-06-18`）

**静态文件读取优先级：** `PAPER_DB_STATIC_EXPORT_DIR` → `PAPER_DB_STATIC_BASE` / `PAPER_DB_STATIC_BASE_URL`

### 认证方式

#### 静态 Bearer

使用 `--mcp-access-token` 或 `MCP_ACCESS_TOKEN`；请求头格式为 `Authorization: Bearer <token>`。对 `/mcp` 和 `/mcp-sse` 均生效。

#### GitHub OAuth

设置 `MCP_AUTH_MODE=github-oauth`。OAuth 仅对 Streamable HTTP 的 `/mcp` 接口生效。

必需的环境变量：

- `MCP_PUBLIC_BASE_URL`
- `GITHUB_OAUTH_CLIENT_ID`
- `GITHUB_OAUTH_CLIENT_SECRET`
- `MCP_GITHUB_ALLOWED_USER_IDS`（GitHub 用户数字 ID，必填）
- `MCP_ACCESS_TOKEN`

OAuth 路由：`/.well-known/`、`/authorize`、`/token`、`/register`、`/auth/callback`、`/consent`

> **注意：** MCP token、advanced-search token 和 admin token 是相互独立的凭据。

### MCP 工具

<details>
<summary><code>search_papers(query, limit=10) → list[dict]</code></summary>

全文搜索论文（按相关性排序）。当你只有主题关键词时使用此工具。

返回：`paper_id`、`title`、`year`、`venue`、`snippet_markdown`。
</details>

<details>
<summary><code>search_papers_by_keyword(keyword, limit=10) → list[dict]</code></summary>

按关键词/标签搜索论文（精确匹配）。当你知道具体关键词或标签时使用此工具。

返回：`paper_id`、`title`、`year`、`venue`、`snippet_markdown`。
</details>

<details>
<summary><code>search_papers_semantic(query, top_n=10, mmr_lambda=None, rerank="auto", filters=None) → dict</code></summary>

运行完整的 advanced semantic search 流程并返回结果。需要 MCP 服务器配置了 advanced search。

- `mmr_lambda`：MMR 多样性参数（`None` 时使用服务器默认值）
- `rerank`：重排序模式（默认为 `"auto"`）
- `filters`：可选的结构化过滤参数
</details>

<details>
<summary><code>get_paper_metadata(paper_id) → dict</code></summary>

获取论文元数据和可用的 summary 模板。在请求 summary 之前先调用此工具，以了解有哪些模板可用。

返回：`paper_id`、`title`、`year`、`venue`、`doi`、`arxiv_id`、`openreview_id`、`paper_pw_url`、`preferred_summary_template`、`available_summary_templates`、`has_bibtex`。
</details>

<details>
<summary><code>get_paper_bibtex(paper_id) → dict</code></summary>

获取论文的 BibTeX 数据。包含论文元数据中的规范 DOI 和 BibTeX 条目文本。

返回：`paper_id`、`doi`、`bibtex_raw`、`bibtex_key`、`entry_type`。
</details>

<details>
<summary><code>get_paper_summary(paper_id, template=None, max_chars=None) → str</code></summary>

以原始字符串形式获取 summary JSON。未指定 `template` 时使用首选模板。返回完整的 JSON 内容（非 URL）。
</details>

<details>
<summary><code>get_paper_summary_keys(paper_id, template=None, max_depth=2, include_preview=False) → dict</code></summary>

按文档顺序获取递归的 summary 键路径。

- `max_depth`：最大嵌套深度（默认 2）
- `include_preview`：设为 `True` 时包含值的预览

返回：`paper_id`、`template`、`keys`（键路径列表，含类型和可选的预览值）。
</details>

<details>
<summary><code>get_paper_summary_key(paper_id, key, template=None, max_chars=None) → dict</code></summary>

获取 summary 中单个指定路径的节点。

返回：`paper_id`、`template`、`key`、`value`、`type`。
</details>

<details>
<summary><code>get_paper_source(paper_id, max_chars=None) → str</code></summary>

获取源 markdown 文本。内容可能较大，可使用 `max_chars` 限制大小。
</details>

<details>
<summary><code>get_paper_source_outline(paper_id) → dict</code></summary>

获取源 markdown 的大纲，以章节范围的形式返回。

返回：`paper_id`、`sections`（列表，每项包含 `heading`、`level`、`start_line`、`end_line`）。
</details>

<details>
<summary><code>get_paper_source_lines(paper_id, start_line, end_line) → dict</code></summary>

获取源 markdown 的指定行范围（1 起始，闭区间）。

返回：`paper_id`、`start_line`、`end_line`、`content`。
</details>

<details>
<summary><code>get_paper_translation_outline(paper_id, lang) → dict</code></summary>

获取翻译后 markdown 的大纲，以章节范围的形式返回。

返回：`paper_id`、`lang`、`sections`（列表，每项包含 `heading`、`level`、`start_line`、`end_line`）。
</details>

<details>
<summary><code>get_paper_translation_lines(paper_id, lang, start_line, end_line) → dict</code></summary>

获取翻译后 markdown 的指定行范围（1 起始，闭区间）。

返回：`paper_id`、`lang`、`start_line`、`end_line`、`content`。
</details>

<details>
<summary><code>get_database_stats() → dict</code></summary>

获取数据库统计信息。返回总数、按年/月的分布，以及 top facets（作者、会议/期刊、关键词、机构、标签）。
</details>

<details>
<summary><code>list_top_facets(category, limit=20) → list[dict]</code></summary>

列出 top facet 值。

- `category`：可选值 `author`、`venue`、`keyword`、`institution`、`tag`

返回：`[{ value, paper_count }]`。
</details>

<details>
<summary><code>filter_papers(author=None, venue=None, year=None, keyword=None, tag=None, limit=10) → list[dict]</code></summary>

按结构化字段筛选论文。适用于按作者、会议/期刊、年份、关键词或标签进行精确筛选。

返回：`paper_id`、`title`、`year`、`venue`。
</details>

### MCP 资源（URI 访问）

| URI | 说明 | MIME 类型 |
|-----|------|-----------|
| `paper://{paper_id}/metadata` | 论文元数据，包括标题、作者、年份、会议/期刊、DOI 和可用的 summary 模板 | `application/json` |
| `paper://{paper_id}/summary` | 使用首选模板的论文 summary | `application/json` |
| `paper://{paper_id}/summary/{template}` | 使用指定模板的论文 summary | `application/json` |
| `paper://{paper_id}/source` | 论文的源 markdown 内容 | `text/markdown` |
| `paper://{paper_id}/translation/{lang}` | 指定语言的翻译后 markdown 内容 | `text/markdown` |
