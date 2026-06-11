[← Back to README](../README.md)

# Advanced Workflows

## Incremental PDF Library Workflow

Keep a growing PDF library in sync without reprocessing everything. This workflow identifies new PDFs, processes only the missing ones, and merges them back into the existing library.

```bash
# 1) Compare processed JSON vs new PDF library to find missing PDFs
uv run deepresearch-flow paper db compare \
  --input-a ./paper_infos.json \
  --pdf-root-b ./pdfs_new \
  --output-only-in-b ./pdfs_todo.txt

# 2) Stage the missing PDFs for OCR
uv run deepresearch-flow paper db transfer-pdfs \
  --input-list ./pdfs_todo.txt \
  --output-dir ./pdfs_todo \
  --copy

# (optional) use --move instead of --copy
# uv run deepresearch-flow paper db transfer-pdfs --input-list ./pdfs_todo.txt --output-dir ./pdfs_todo --move

# 3) OCR the missing PDFs (use your OCR tool; write markdowns to ./md_todo)

# 4) Extract or relocate assets matched against the new PDF library
uv run deepresearch-flow paper db extract \
  --input-json ./paper_infos.json \
  --pdf-root ./pdfs_new \
  --output-json ./paper_infos_matched.json

uv run deepresearch-flow paper db extract \
  --md-source-root ./mds \
  --output-md-root ./mds_matched \
  --pdf-root ./pdfs_new

uv run deepresearch-flow paper db extract \
  --md-translated-root ./translated \
  --output-md-translated-root ./translated_matched \
  --pdf-root ./pdfs_new \
  --lang zh

# 5) Translate + extract summaries for the new OCR markdowns
uv run deepresearch-flow translator translate \
  --input ./md_todo \
  --target-lang zh \
  --model openai/gpt-4o-mini

uv run deepresearch-flow paper extract \
  --input ./md_todo \
  --model openai/gpt-4o-mini

# 6) Merge and serve the new library (multi-input)
uv run deepresearch-flow paper db serve \
  --input ./paper_infos_matched.json \
  --input ./paper_infos_new.json \
  --md-root ./mds_matched \
  --md-root ./md_todo \
  --md-translated-root ./translated_matched \
  --md-translated-root ./md_todo \
  --pdf-root ./pdfs_new
```

## Merging Paper Data

### Merge Paper JSONs

Combine multiple paper info JSON files or templates.

```bash
# Merge multiple libraries using the same template
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos_a.json \
  --inputs ./paper_infos_b.json \
  --output ./paper_infos_merged.json

# Merge multiple templates from the same library (first input wins on shared fields)
uv run deepresearch-flow paper db merge templates \
  --inputs ./simple.json \
  --inputs ./deep_read.json \
  --output ./paper_infos_templates.json
```

Note: `paper db merge` is now split into `merge library` and `merge templates`.

### Merge Multiple Databases (PDF + Markdown + BibTeX)

When you need to combine complete databases including PDFs, markdown files, JSON metadata, and BibTeX references.

```bash
# 1) Copy PDFs into a single folder
rsync -av ./pdfs_a/ ./pdfs_merged/
rsync -av ./pdfs_b/ ./pdfs_merged/

# 2) Copy Markdown folders into a single folder
rsync -av ./md_a/ ./md_merged/
rsync -av ./md_b/ ./md_merged/

# 3) Merge JSON libraries
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos_a.json \
  --inputs ./paper_infos_b.json \
  --output ./paper_infos_merged.json

# 4) Merge BibTeX files
uv run deepresearch-flow paper db merge bibtex \
  -i ./library_a.bib \
  -i ./library_b.bib \
  -o ./library_merged.bib
```

### Merge BibTeX Files

Merge individual BibTeX files independently.

```bash
uv run deepresearch-flow paper db merge bibtex \
  -i ./library_a.bib \
  -i ./library_b.bib \
  -o ./library_merged.bib
```

Duplicate keys keep the entry with the most fields; ties keep the first input order.

### Recommended: Merge Templates Then Filter by BibTeX

For the same library processed with multiple templates, merge all results then filter against a BibTeX reference.

```bash
# 1) Merge templates for the same library
uv run deepresearch-flow paper db merge templates \
  --inputs ./deep_read.json \
  --inputs ./simple.json \
  --output ./all.json

# 2) Filter the merged set with BibTeX
uv run deepresearch-flow paper db extract \
  --input-bibtex ./library.bib \
  --json ./all.json \
  --output-json ./library_filtered.json \
  --output-csv ./library_filtered.csv
```

## Supplementing Missing Templates and Translations

When processing a large library, some papers may lack templates (e.g., deep read results) or translations. These workflows let you identify and fill those gaps—either by extracting from source markdown or by supplementing an existing snapshot in-place.

### Method 1: Identify Gaps, Extract, and Rebuild

```bash
# 1) Check missing templates in snapshot
uv run deepresearch-flow paper db snapshot show-missing \
  --snapshot-db ./dist/paper_snapshot.db

# 2) Export papers missing specific template (with file paths for extraction)
uv run deepresearch-flow paper db snapshot export-missing \
  --snapshot-db ./dist/paper_snapshot.db \
  --type template \
  --template deep_read \
  --static-export-dir ./dist/paper-static \
  --output ./missing_deep_read.json \
  --txt-output ./missing_ids.txt \
  --output-paths ./extractable_paths.txt

# 3) Extract missing templates (only for papers with source markdown)
uv run deepresearch-flow paper extract \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read \
  --input-list ./extractable_paths.txt \
  --output ./deep_read_supplement.json

# 4) Merge with existing paper_infos.json
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos.json \
  --inputs ./deep_read_supplement.json \
  --output ./paper_infos_complete.json

# 5) Rebuild snapshot with complete data
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos_complete.json \
  --bibtex ./papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot_complete.db \
  --static-export-dir ./dist/paper-static-complete
```

### Method 2: Supplement Without Rebuilding

Update an existing snapshot in-place without a full rebuild.

```bash
# Supplement missing templates for existing papers (in-place)
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

See [Snapshot Management](snapshot-management.md) for details on snapshot build and serve workflows.

### Supplement Missing Translations

Add translations for papers that lack them, then update the snapshot.

```bash
# 1) Export papers missing Chinese translation (with file paths)
uv run deepresearch-flow paper db snapshot export-missing \
  --snapshot-db ./dist/paper_snapshot.db \
  --type translation \
  --lang zh \
  --static-export-dir ./dist/paper-static \
  --output-paths ./to_translate_paths.txt

# 2) Translate missing papers
uv run deepresearch-flow translator translate \
  --input ./docs \
  --target-lang zh \
  --model openai/gpt-4o-mini \
  --input-list ./to_translate_paths.txt \
  --output-dir ./docs_translated

# 3) Rebuild or supplement snapshot with new translations
uv run deepresearch-flow paper db snapshot build ...
# Or use snapshot supplement if only adding translations
```

Useful export types for `paper db snapshot export-missing`:

| Type | Description |
|------|-------------|
| `--type source_md` | Papers without source markdown |
| `--type pdf` | Papers without PDF |
| `--type translation --lang zh` | Papers without Chinese translation |

## Related Documentation

- [Deployment Guide](deployment.md) – Serve the processed library via HTTP
- [Snapshot Management](snapshot-management.md) – Build, query, and maintain paper snapshots
