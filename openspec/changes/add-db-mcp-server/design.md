## Context
- The snapshot API serves read-only data from `paper_snapshot.db` via HTTP endpoints and powers the static frontend.
- LLM clients require a standardized MCP interface to search and read paper data.
- The MCP transport MUST use Streamable HTTP (no SSE) to align with the latest protocol guidance.
- Static summaries and markdown assets are hosted in static storage and MUST be proxied by the MCP server (no URL leakage).

## Goals / Non-Goals

### Goals
- Expose MCP tools/resources for snapshot DB queries and paper content reads.
- Ensure tools/resources return **extracted content**, not static asset URLs.
- Provide clear tool descriptions so LLMs can select the correct tool.
- Support both full-text search and keyword/tag search.
- Mount MCP as a sub-application under `/mcp` in the existing Starlette API service.
- Enforce read-only access and apply snapshot API limits to MCP search operations.

### Non-Goals
- No write operations or data mutation.
- No SSE or WebSocket transport.
- No authentication/authorization changes in this proposal.
- No MCP prompts in this change set.

## Decisions
- Use the official MCP Python SDK (`FastMCP`) with Streamable HTTP transport and mount at `/mcp`.
- Reuse snapshot DB query semantics (FTS search, facet lookup) and limits from the snapshot API.
- Open SQLite connections in read-only mode for each request to avoid shared mutable state.
- Proxy static assets internally: construct asset URLs/paths, fetch/parse content, and return text (never return URLs).
- Ensure tool metadata includes strong titles/descriptions and JSON Schemas for inputs.

## Alternatives Considered
- SSE transport: rejected due to MCP deprecation in favor of Streamable HTTP.
- STDIO-only server: rejected because the MCP server must be reachable over HTTP.
- Separate MCP microservice: rejected to avoid duplicating config/deployment; in-process mounting simplifies ops.

## Risks / Trade-offs
- Some MCP clients may not yet support Streamable HTTP; mitigate by documenting proxy usage.
- Large markdown resources can increase response size; mitigate by truncation limits and `max_chars` arguments.
- High concurrency could stress SQLite; mitigate with read-only connections and conservative query limits.
- Proxying static assets adds HTTP latency; mitigate with short timeouts and optional in-memory caching.

## Migration Plan
- Add MCP server module and mount it under `/mcp` in the snapshot API.
- Add MCP SDK dependency to project metadata.
- Document client connection instructions and required environment settings.
- No data migration required.

## Open Questions
- Should summary extraction return plain text only, or include minimal structured fields (e.g., `summary`, `key_points`)?
- Should MCP endpoints require API tokens in a follow-up change?
