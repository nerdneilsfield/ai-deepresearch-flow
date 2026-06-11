# Snapshot Management

[← Back to README](../README.md)

## Building a Production Snapshot

Build a production-quality snapshot (SQLite + static assets):

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos.json \
  --bibtex ./papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static
```

Notes:
- Build host must be able to read the original PDF/Markdown roots.
- CDN server only needs the exported directory (e.g. `/data/paper-static`).
- `--output-embed-db` can build the LanceDB index in the same pass.

## Supplement Missing Templates

If existing papers are missing templates (e.g., `deep_read`), supplement them without rebuilding:

```bash
# Supplement in-place
uv run deepresearch-flow paper db snapshot supplement \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./deep_read_supplement.json \
  --in-place

# Or output to new location
uv run deepresearch-flow paper db snapshot supplement \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./deep_read_supplement.json \
  --output-db ./dist/paper_snapshot_supplemented.db \
  --output-static-dir ./dist/paper-static-supplemented
```

Notes:
- `--md-root` and `--md-translated-root` are optional — only needed when resolving markdown from local dirs.
- Also accepts `--bibtex` and `--pdf-root` (optional).

## Supplement Missing Translations

Export papers missing a translation, translate them, then rebuild or supplement:

```bash
# 1) Export papers missing Chinese translation
uv run deepresearch-flow paper db snapshot export-missing \
  --snapshot-db ./dist/paper_snapshot.db \
  --type translation --lang zh \
  --static-export-dir ./dist/paper-static \
  --output-paths ./to_translate_paths.txt

# 2) Translate
uv run deepresearch-flow translator translate \
  --input ./docs --target-lang zh \
  --model openai/gpt-4o-mini \
  --input-list ./to_translate_paths.txt \
  --output-dir ./docs_translated

# 3) Rebuild or supplement snapshot
uv run deepresearch-flow paper db snapshot build ...
```

Useful export types: `--type source_md`, `--type pdf`, `--type translation --lang zh`

## Adding New Papers (Update)

If you have new papers to add to the snapshot:

```bash
# Add new papers in-place
uv run deepresearch-flow paper db snapshot update \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./new_papers.json \
  -b ./new_papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs_translated \
  --pdf-root ./pdfs \
  --in-place

# Or output to new location
uv run deepresearch-flow paper db snapshot update \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./new_papers.json \
  -b ./new_papers.bib \
  --md-root ./docs \
  --output-db ./dist/paper_snapshot_updated.db \
  --output-static-dir ./dist/paper-static-updated
```

### Difference: Supplement vs Update

| Command | Scope | Behavior |
|---------|-------|----------|
| **supplement** | Existing papers only | Adds missing templates/translations for papers already in the snapshot |
| **update** | New papers only | Adds papers not yet in the snapshot |

## Snapshot Migration (Legacy → DOI/BibTeX)

### Recommended: Migrate Schema In-Place (No Data Loss)

If your existing snapshot was built before DOI/BibTeX support:

```bash
# In-place migration with timestamped backup
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --bibtex ./papers.bib \
  --static-export-dir ./dist/paper-static \
  --in-place

# Or copy to new location
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --bibtex ./papers.bib \
  --static-export-dir ./dist/paper-static \
  --output-db ./dist/paper_snapshot_v2.db

# Schema-only migration (no BibTeX enrichment)
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --in-place
```

The migrate command will:

1. Create a timestamped backup (unless `--no-backup`)
2. Add `doi` column to `paper` table (if missing)
3. Create `paper_bibtex` table (if missing)
4. Match papers with BibTeX entries and populate DOI/BibTeX data
5. Update static export index metadata

Features:

- **No data loss**: Uses `ALTER TABLE` to upgrade schema
- **Timestamped backups**: `.bak_YYYYMMDD_HHMMSS` format
- **BibTeX enrichment**: Matches papers with BibTeX, extracts DOI metadata
- **Static export update**: Updates `paper_index.json`

### Alternative: Rebuild with Previous Snapshot

If you need to rebuild from scratch while preserving identity continuity:

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos_complete.json \
  --bibtex ./papers.bib \
  --output-db ./dist/paper_snapshot_v2.db \
  --static-export-dir ./dist/paper-static-v2 \
  --previous-snapshot-db ./dist/paper_snapshot.db
```

Notes:

- `--md-root`, `--md-translated-root`, `--pdf-root` are optional.
- Current inputs with DOI/BibTeX take priority; otherwise inherits from `--previous-snapshot-db`.
- **Warning**: Only includes papers from input JSON — ensure all papers are included.
