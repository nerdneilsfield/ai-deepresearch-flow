## ADDED Requirements

### Requirement: Streamable HTTP MCP endpoint
The system SHALL expose an MCP server over Streamable HTTP under the `/mcp` path.
The MCP server SHALL accept JSON-RPC 2.0 POST requests for initialization, tool invocation, and resource reads.
The MCP server SHALL respond with `Content-Type: application/json` and SHALL NOT initiate SSE streams.
The MCP server SHALL return HTTP 405 for GET requests at `/mcp` (SSE not supported).
The MCP server SHALL validate the `MCP-Protocol-Version` header when present and MUST return HTTP 400 for invalid or unsupported versions; if the header is absent, the server SHALL assume protocol version `2025-03-26`.
The MCP server SHALL be stateless and SHALL NOT issue `Mcp-Session-Id` headers.
The MCP server SHALL expose only tools and resources and SHALL NOT expose MCP prompts.
The MCP server SHALL populate tool `title` and `description` fields to clearly explain usage to LLMs.

#### Scenario: Initialize MCP capabilities
- **WHEN** an MCP client sends an `initialize` request to `/mcp`
- **THEN** the server returns capabilities including tools and resources only

#### Scenario: GET not supported
- **WHEN** a client issues an HTTP GET to `/mcp`
- **THEN** the server responds with HTTP 405 Method Not Allowed

#### Scenario: Invalid protocol version
- **WHEN** a client sends a request with an unsupported `MCP-Protocol-Version`
- **THEN** the server responds with HTTP 400 Bad Request

#### Scenario: Tool descriptions for LLM guidance
- **WHEN** a client requests `tools/list`
- **THEN** each tool includes a clear `title` and `description` explaining when to use it

### Requirement: Transport security validation
The MCP server SHALL validate the `Origin` header against a configured allowlist for `/mcp` requests.
The MCP server SHALL reject disallowed origins with HTTP 403.

#### Scenario: Reject disallowed origin
- **WHEN** a request to `/mcp` contains an `Origin` not in the allowlist
- **THEN** the server responds with HTTP 403 Forbidden

### Requirement: Search tools
The MCP server SHALL provide a `search_papers` tool for full-text search.
The MCP server SHALL provide a `search_papers_by_keyword` tool for keyword/tag search.
The search tools SHALL return `paper_id`, `title`, `year`, `venue`, and `snippet_markdown` when a query is provided.
The search tools SHALL honor a `limit` parameter and default to a safe value.

#### Scenario: Full-text search
- **WHEN** a client calls `search_papers` with a query string
- **THEN** the server returns matching papers ordered by relevance with `snippet_markdown`

#### Scenario: Keyword search
- **WHEN** a client calls `search_papers_by_keyword` with `keyword = "transformer"`
- **THEN** the server returns papers tagged with matching keyword values

### Requirement: Paper detail tools with static asset proxy
The MCP server SHALL provide tools `get_paper_metadata`, `get_paper_summary`, and `get_paper_source`.
The metadata tool SHALL return structured fields for the requested `paper_id` and include `preferred_summary_template` plus `available_summary_templates`.
The summary tool SHALL accept an optional `template` parameter; when omitted, it SHALL use `preferred_summary_template`.
The summary tool SHALL fetch summary content from static assets (using internal URL or local filesystem read) and SHALL return the **full JSON string** content to the client (no URL leakage).
The summary tool description SHALL instruct LLMs to call `get_paper_metadata` first to discover available templates.
The source tool SHALL fetch source markdown from static assets and SHALL return markdown text directly.
The source tool description SHALL warn that content can be large and that `max_chars` can limit size.

#### Scenario: Metadata includes templates
- **WHEN** a client calls `get_paper_metadata` for an existing `paper_id`
- **THEN** the response includes `preferred_summary_template`
- **AND** the response includes `available_summary_templates`

#### Scenario: Summary proxy fetch
- **WHEN** a client calls `get_paper_summary` with `template = "deep_read"`
- **THEN** the server resolves the static asset location internally
- **AND** fetches the summary content
- **AND** returns extracted text content (not a URL)

#### Scenario: Missing source content
- **WHEN** a client calls `get_paper_source` for a paper without source markdown
- **THEN** the server returns an explicit "not available" response

### Requirement: Facet discovery tool
The MCP server SHALL provide a `list_top_facets` tool that returns top values for authors, venues, keywords, institutions, and tags with counts.

#### Scenario: Top authors
- **WHEN** a client calls `list_top_facets` with category `author` and limit 20
- **THEN** the server returns the top authors sorted by `paper_count` descending

### Requirement: Paper resources with template-aware summaries
The MCP server SHALL expose resources using the scheme `paper://{paper_id}/{resource}` for:
- `metadata`
- `summary` and `summary/{template}`
- `source`
- `translation/{lang}`
The resource handlers SHALL resolve static asset locations internally and SHALL return content directly (no URLs).
The `paper://{paper_id}/summary` resource SHALL return the preferred summary template JSON by default.

#### Scenario: Read summary resource with template
- **WHEN** a client reads `paper://123/summary/deep_read`
- **THEN** the server returns the extracted summary content for template `deep_read`

#### Scenario: Read translation resource
- **WHEN** a client reads `paper://123/translation/zh` and a translation exists
- **THEN** the server returns the translated markdown text

### Requirement: Content size limits
The summary and source tools SHALL support a `max_chars` parameter to limit response size.
When content exceeds `max_chars`, the server SHALL truncate and append a truncation marker.
Resource reads SHALL also apply a default truncation limit.

#### Scenario: Tool truncates large content
- **WHEN** a client calls `get_paper_source` with `max_chars = 10000`
- **AND** the source content exceeds 10000 characters
- **THEN** the server returns the first 10000 characters and appends a truncation marker

#### Scenario: Resource truncates large content
- **WHEN** a client reads `paper://123/source` and the content exceeds the default limit
- **THEN** the server returns truncated content with a truncation marker

### Requirement: Asset fetch errors
The MCP server SHALL return structured errors for missing templates, missing assets, fetch timeouts, and parse failures.
The error response SHALL include `paper_id` and `template` (when applicable), plus a human-readable message.

#### Scenario: Template not available
- **WHEN** a client requests `get_paper_summary` with a template not available for that paper
- **THEN** the server returns an error indicating `template_not_available`
- **AND** includes the `available_summary_templates`

#### Scenario: Asset fetch failure
- **WHEN** the server fails to fetch a static asset (404 or timeout)
- **THEN** the server returns an error indicating `asset_fetch_failed`

### Requirement: Safety limits and read-only access
The MCP server SHALL open the snapshot database in read-only mode.
The MCP server SHALL enforce query limits aligned with the snapshot API defaults: max query length 500, max page_size 100, and max pagination offset 10,000 items.

#### Scenario: Excessive query length
- **WHEN** a client issues a search with `query` longer than 500 characters
- **THEN** the server returns a validation error

#### Scenario: Excessive page size
- **WHEN** a client requests a `page_size` above 100
- **THEN** the server returns a validation error
