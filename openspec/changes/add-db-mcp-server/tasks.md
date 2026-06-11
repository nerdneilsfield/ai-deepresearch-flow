> **Archived historical change:** This OpenSpec change documents the original MCP implementation. Current MCP public docs supersede its full-content tools/resources: use `get_paper_content_outline`, `get_paper_content_window`, `get_paper_summary_keys`, and `get_paper_summary_value`; OAuth Streamable HTTP is `/oauth/mcp`; OAuth SSE `/oauth/mcp-sse` is currently absent/unsupported.

## 1. Historical Implementation Tasks (not current)
- [ ] 1.1 Add MCP server module for snapshot DB (FastMCP, Streamable HTTP only)
- [ ] 1.2 Implement tools: `search_papers`, `search_papers_by_keyword`, `get_paper_metadata`, historical removed `get_paper_summary`, historical removed `get_paper_source`, `get_database_stats`, `list_top_facets`, `filter_papers`
- [ ] 1.3 Implement resources: `paper://{paper_id}/metadata` (small compatibility resource), historical removed `paper://{paper_id}/summary`, historical removed `paper://{paper_id}/summary/{template}`, historical removed `paper://{paper_id}/source`, historical removed `paper://{paper_id}/translation/{lang}`
- [ ] 1.4 Implement static asset proxy reads (summary/source/translation) and return extracted content (no URL leakage)
- [ ] 1.5 Add tool metadata: clear titles/descriptions and JSON Schema input constraints for LLM guidance
- [ ] 1.6 Mount MCP server under `/mcp` in snapshot API app and wire snapshot DB path + limits
- [ ] 1.7 Enforce read-only DB connections and guardrails (q length, page_size, max offset, limit defaults)
- [ ] 1.8 Document MCP Streamable HTTP usage and client connection examples
- [ ] 1.9 Add MCP SDK dependency and update packaging metadata
- [ ] 1.10 Run `openspec validate add-db-mcp-server --strict`
