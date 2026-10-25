# Snapshot Management

[← Back to README](../../README.md)

## Building a Production Snapshot

Build a production-quality snapshot (SQLite + static assets) after the flow in
[From PDFs to a snapshot](pipeline.md) has produced repaired summary JSON. This command consumes those JSON
bundles; it does not create them from PDFs or BibTeX.

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./deep_read.json \
  --bibtex ./papers.bib \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --pdf-root ./pdf \
  --output-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static
```

Notes:
- Add one `--input ./<template>.json` for each additional repaired
  summary template.
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

## Repairing deployed metadata (delta upload)

Use this after a fix to the build logic, for example BibTeX matching or the source of titles and authors. It updates the deployed data without re-uploading the whole static export.

You do not need to rerun OCR, translation or extraction. Rebuild locally, upload only the static files that changed, then rewrite the remote database with `api update`.

This works because of how static files are named:

- `pdf/`, `images/`, `md/` and `md_translate/` are named by content hash. A file with the same name can stay as it is on the server. This also holds for PDFs you compressed after export.
- `summary/` and `manifest/` are named by `paper_id` and contain metadata such as the title. A file with the same name can still change, so compare the content.

On a full export of about 3 GB, the delta archive is about 2.4 MB.

The commands below assume the project directory `./myproj`, laid out as in [From PDFs to a snapshot](pipeline.md). The deployed database and export directory are `myproj/dist/myproj.db` and `myproj/dist/myproj-export/`.

### 1. Deploy the new backend

Deploy the new backend before the other steps. With the old backend, `api update` writes BibTeX as an empty shell with no fields.

### 2. Rebuild locally

Use the same JSON, BibTeX and roots as before, and write the new build to the `rebuild/` directory. Point `--previous-snapshot-db` at the deployed database so that `paper_id` values stay stable.

```bash
P=./myproj
uv run deepresearch-flow paper db snapshot build \
  -i $P/simple.json -i $P/deep_read.json -b $P/myproj.bib \
  --md-root $P/md_simple --md-translated-root $P/md_base64_translated --pdf-root $P/pdf \
  --output-db rebuild/myproj.db --static-export-dir rebuild/static \
  --previous-snapshot-db $P/dist/myproj.db
```

### 3. Build the delta archive

`OLD` is the export directory you already uploaded. Its content must match the server. `NEW` is the new static directory. The archive `delta.tar.gz` and the file list `delta.list` are written under `rebuild/`.

```bash
R=$(realpath rebuild)
OLD=$(realpath $P/dist/myproj-export)
NEW=$R/static
cd "$NEW"
find . -type f | while read -r f; do
  if [ ! -e "$OLD/$f" ]; then echo "$f"                                    # new file
  elif [[ $f == *.json ]] && ! cmp -s "$f" "$OLD/$f"; then echo "$f"; fi   # changed summary/manifest
done > "$R/delta.list"
cut -d/ -f2 "$R/delta.list" | sort | uniq -c                              # count per directory
tar -C "$NEW" -czf "$R/delta.tar.gz" -T "$R/delta.list"
```

### 4. Extract on the server

In the static directory, extract over the existing files:

```bash
tar -xzf delta.tar.gz
```

You do not need to delete old files. The database no longer refers to them.

### 5. Rewrite the remote metadata

Run with `--dry-run` first to see how many papers will be updated, then run it for real:

```bash
uv run deepresearch-flow paper db api update \
  --snapshot-db rebuild/myproj.db \
  --static-export-dir rebuild/static \
  --config remote.toml --dry-run
```

`api update` rewrites only the metadata of papers already on the remote: title, authors, BibTeX, facets and the search index. It does not touch static files or vectors.

### 6. Handle papers whose `paper_id` changed

A paper that had no BibTeX match before and matches now gets a new `paper_id`. `api update` skips it because the remote does not have it yet. Find these papers with:

```bash
OLD_DB=$P/dist/myproj.db NEW_DB=rebuild/myproj.db python3 - <<'PY'
import os, sqlite3
old = {r[0] for r in sqlite3.connect(os.environ["OLD_DB"]).execute("select paper_id from paper")}
new = {r[0] for r in sqlite3.connect(os.environ["NEW_DB"]).execute("select paper_id from paper")}
print("added:", sorted(new - old)); print("to delete:", sorted(old - new))
PY
```

Push the added papers with `api push`. For each paper to delete, call `DELETE /papers/{paper_id}` on the admin API.

### Notes

- The delta is computed against the local `OLD` directory. If files on the server were changed and not synced back, the delta is wrong.
- Repeat these steps for each dataset.
