# Change: Persist DOI and BibTeX metadata in snapshot DB

## Why
Snapshot artifacts currently persist search/browse metadata but do not persist a first-class DOI field or a dedicated BibTeX table. This causes metadata loss across rebuilds and forces MCP/API metadata to return empty DOI values even when input BibTeX exists.

## What Changes
- Add `doi` to snapshot `paper` metadata.
- Add a dedicated `paper_bibtex` table to persist per-paper BibTeX raw text and key/type metadata.
- Define `bibtex_raw` as deterministic entry text generated from parsed BibTeX entry data (not byte-identical source file slices).
- Update snapshot build/update pipelines to write DOI + BibTeX metadata into the snapshot DB.
- During rebuild with `--previous-snapshot-db`, inherit DOI/BibTeX from the previous snapshot for matched `paper_id` when current inputs do not provide these fields.
- Add a new API endpoint: `GET /api/v1/papers/{paper_id}/bibtex`.
- Define explicit API error codes to distinguish `paper_not_found` vs `bibtex_not_found`.
- Update MCP metadata behavior to return persisted DOI value instead of always returning `null`.
- Expose BibTeX availability in MCP metadata via `has_bibtex`.
- Add an MCP tool `get_paper_bibtex(paper_id)` to return persisted BibTeX content directly for agent usage.

## Impact
- Affected specs: `paper-db-snapshot`, `paper-db-api`, `paper-db-mcp`
- Affected code:
  - `python/deepresearch_flow/paper/snapshot/schema.py`
  - `python/deepresearch_flow/paper/snapshot/builder.py`
  - `python/deepresearch_flow/paper/snapshot/update.py`
  - `python/deepresearch_flow/paper/snapshot/api.py`
  - `python/deepresearch_flow/paper/snapshot/mcp_server.py`
  - snapshot tests and API contract docs
