## Context
Users want to compare outputs across the OCR pipeline (JSON, PDF, Markdown, translated Markdown) and identify missing artifacts. Matching must be consistent with the `paper db serve` logic so results align with what the UI shows. The matching/index logic now lives in `deepresearch_flow.paper.db_ops` after the web refactor.

## Goals / Non-Goals
- Goals:
  - Provide `paper db compare` to compare any two datasets (A/B).
  - Use the same matching heuristics as db serve.
  - Provide clear counts and sample lists in the terminal plus full CSV export.
  - Support language-specific translated comparisons.
- Non-Goals:
  - UI changes in `paper db serve` (CLI-only for now).
  - Automated fixes or generation of missing files.

## Decisions
- CLI shape: `paper db compare` with A/B inputs.
  - A side supports: `--a-json` (repeatable), `--a-pdf-root`, `--a-md-root`, `--a-md-translated-root`.
  - B side supports: `--b-json`, `--b-pdf-root`, `--b-md-root`, `--b-md-translated-root`.
  - Language control: `--lang` is REQUIRED when either side is translated Markdown; comparison is per-language.
  - Output control: `--limit` for sample list size, `--output-csv` for full CSV.
- Matching logic: reuse data-layer helpers in `deepresearch_flow.paper.db_ops`.
  - JSON-to-file resolution uses `_resolve_source_md`, `_resolve_pdf`, and translated index lookup by resolved MD basename.
  - File-only sets are built using `_build_file_index` and `_build_translated_index` to maintain filename/title matching.
  - Matching fields are prepared via `_prepare_paper_matching_fields` for parity with `paper db serve`.
- Output structure:
  - Rich table summary with counts for BOTH, ONLY A, ONLY B.
  - Sample lists for A-only and B-only (limit applied).
  - CSV includes every item with `side`, `match_status`, `match_type`, `title`, `source_hash`, `path`, and `lang` when relevant.
  - No JSON output (CSV is the export format).

## Risks / Trade-offs
- Matching heuristics are tied to db serve behavior in `db_ops`; changes there may affect compare results.
- Translated comparisons depend on resolved MD basenames, which may exclude translated files not aligned to a source MD.

## Migration Plan
- None; new command only.

## Open Questions
- None.
