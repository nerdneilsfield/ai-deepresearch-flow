> **Archived historical change:** This OpenSpec change documents the original MCP implementation. Current MCP public docs supersede its full-content tools/resources: use `get_paper_content_outline`, `get_paper_content_window`, `get_paper_summary_keys`, and `get_paper_summary_value`; OAuth Streamable HTTP is `/oauth/mcp`; OAuth SSE `/oauth/mcp-sse` is currently absent/unsupported.

# Change: Add MCP server for snapshot paper DB (Streamable HTTP)

## Why
- Provide LLM clients with a standardized MCP interface to query and read the snapshot paper database.
- Support the MCP Streamable HTTP transport (no SSE) to align with the latest protocol guidance and simplify HTTP integration.

## What Changes
- Add an MCP server mounted under `/mcp` using Streamable HTTP transport.
- Map snapshot DB read-only capabilities to MCP tools/resources/prompts, including full-text and keyword search.
- Align MCP search limits with snapshot API constraints to protect the database from expensive queries.
- Document MCP client connection and expected payloads for Streamable HTTP.

## Impact
- Affected specs: paper-db-mcp (new)
- Affected code: python/deepresearch_flow/paper/snapshot/api.py, python/deepresearch_flow/paper/snapshot/mcp_server.py
- External dependencies: mcp Python SDK (new runtime dependency)
