## Context
The snapshot pipeline already computes identity keys from DOI/arXiv/BibTeX metadata, but these metadata are not stored as queryable first-class fields. Rebuilds can preserve `paper_id` continuity through aliases, but metadata payloads (especially BibTeX text) are not persisted in a dedicated table.

## Goals / Non-Goals
- Goals:
  - Persist DOI in snapshot paper metadata.
  - Persist BibTeX as a dedicated per-paper table with raw entry text.
  - Keep rebuild UX simple: no intermediate `.bib` export required.
  - Support metadata continuity by inheriting DOI/BibTeX from previous snapshots when inputs are missing fields.
- Non-Goals:
  - Introduce new identifiers beyond DOI in this change (e.g., openreview_id).
  - Change `paper_id` derivation logic.
  - Bulk-export unmatched BibTeX entries.
  - Patch DOI/BibTeX for existing papers through `snapshot update` (update remains add-only).

## Decisions
### 1) Data model
- Extend `paper` table with nullable `doi`.
- Add `paper_bibtex` table:
  - `paper_id TEXT PRIMARY KEY`
  - `bibtex_raw TEXT NOT NULL`
  - `bibtex_key TEXT`
  - `entry_type TEXT`
  - FK to `paper(paper_id)` with cascade delete
- `entry_type` uses lowercased pybtex entry type values (e.g., `article`, `inproceedings`).
- `bibtex_key` stores bare key text (without `bib:` prefix).

### 2) Write path precedence
For each paper in snapshot build/update:
1. Resolve `paper_id` using existing identity logic.
2. Parse-time BibTeX retention: keep deterministic per-entry text alongside parsed BibTeX dict in paper payload (e.g., `paper["bibtex"]["raw_entry"]`).
3. Extract DOI and BibTeX from current inputs.
4. Canonicalize persisted DOI using `canonicalize_doi()` and store canonical value in `paper.doi`.
5. Materialize `bibtex_raw` from parse-retained deterministic entry text (not byte-identical original file fragment).
6. If current input provides DOI/BibTeX, use current values.
7. Else if `--previous-snapshot-db` is provided and same `paper_id` exists there, inherit missing DOI/BibTeX fields from previous snapshot.
8. Persist resolved values to `paper` and `paper_bibtex`.
9. If `bibtex_key` exists and an identity alias of type `bib` is used, `paper_key_alias.paper_key` SHOULD be consistent with `bib:{bibtex_key}`.

This enforces “new input preferred, previous snapshot fallback”.

Consistency note:
- Fallback is per-field. A newly provided DOI may coexist with inherited BibTeX whose DOI field differs.
- This change accepts the mismatch and keeps write path non-blocking.
- Mismatch observability SHOULD be aggregated per build/update run (for example, total mismatch count plus limited samples), rather than emitting one warning line per paper.

### 3) API contract
- Extend `GET /api/v1/papers/{paper_id}` response with `doi`.
- Add `GET /api/v1/papers/{paper_id}/bibtex`:
  - 200 with `{ paper_id, doi, bibtex_raw, bibtex_key, entry_type }` when paper exists
  - `doi` in this payload is sourced from `paper.doi` (canonical persisted DOI), not reparsed from `bibtex_raw`
  - 404 with `{"error":"paper_not_found"}` if `paper_id` does not exist
  - 404 with `{"error":"bibtex_not_found"}` when paper exists but has no persisted BibTeX

### 4) MCP behavior
- `get_paper_metadata` returns stored DOI value when available.
- `get_paper_metadata` includes `has_bibtex: bool` for BibTeX availability discovery.
- Add MCP tool `get_paper_bibtex(paper_id)`:
  - Returns persisted BibTeX payload `{paper_id, doi, bibtex_raw, bibtex_key, entry_type}`.
  - `doi` in this payload is sourced from `paper.doi`.
  - Uses explicit errors aligned with API semantics: `paper_not_found` and `bibtex_not_found`.
- Existing metadata keys remain backward compatible; fields not in scope for this change may still be `null`.

### 5) Search indexing
- Include canonical DOI in FTS metadata corpus so direct DOI queries can match via existing search endpoint.

### 6) Compatibility and migration
- Existing snapshot DBs may not have `paper.doi` or `paper_bibtex`.
- API/MCP access paths should handle missing columns/tables gracefully where practical, but newly built snapshots MUST include the new schema.
- Defensive reads are required in API/MCP to avoid runtime failure when serving old DB files.
- Old-schema fallback target behavior:
  - paper detail responses keep `doi` key and set it to `null` when `paper.doi` is unavailable.
  - MCP metadata keeps `doi` as `null` and `has_bibtex` as `false` when `paper_bibtex` is unavailable.
- Rebuild migration path is the primary mechanism: `snapshot build --previous-snapshot-db <old>` should carry forward DOI/BibTeX by matched `paper_id` without intermediate export.

## Risks / Trade-offs
- Storing raw BibTeX increases DB size.
- `bibtex_raw` is deterministic parser-generated text, not byte-identical source slices.
- Old snapshot compatibility adds conditional code paths in API/MCP.
