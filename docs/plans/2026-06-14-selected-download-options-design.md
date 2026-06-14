# Selected Download Options Design (v0.10.4)

## Background

The Selected page currently downloads every selected paper as a ZIP with a fixed set of assets: PDF, source markdown, default summary, translated markdown, all summary template assets, and images when present in the manifest.

For research workflows this is too coarse. Sometimes the user only wants metadata and selected summary templates for downstream agent/LLM processing. In that case a ZIP of many files is inconvenient; a JSONL file with one paper per line is easier to feed into tools.

This feature targets `v0.10.4`.

## Goals

1. Let the user choose what each selected paper contributes to the batch download.
2. Apply one shared selection policy to all selected papers.
3. Support two output modes:
   - ZIP package for file-oriented exports.
   - JSONL for structured metadata/summary exports.
4. Let the user select summary templates individually, not just default/all.
5. Preserve partial success: a missing asset for one paper should not fail the whole batch.
6. Keep the implementation frontend-only for v0.10.4, using existing paper detail, summary, manifest, and static asset endpoints.

## Non-goals

1. Backend-side batch export endpoint.
2. Per-paper export customization in the same batch.
3. Filtering selected papers by export availability before download.
4. Guaranteeing that every selected template exists for every paper.
5. Exporting binary content inside JSONL.

## Existing Data Sources

### Selected item

`SearchItem` may contain enough lightweight metadata and links:

- `paper_id`
- `paper_index`
- `title`
- `year`
- `venue`
- `authors`
- `summary_url`
- `manifest_url`
- other preview fields

However, not every selected item is guaranteed to contain full detail fields.

### Paper detail

`getPaperDetail(paperId)` returns richer `PaperDetail`, including:

- metadata fields
- `summary_url`
- `summary_urls?: Record<string, string>`
- `manifest_url`
- PDF / markdown / translated markdown URLs

### Summary payload

`getSummaryPayloadCached(paperId, template, url)` returns the raw JSON payload for a summary template. This is the preferred source for JSONL summaries.

### Manifest

`fetchManifest(manifestUrl)` returns file assets suitable for ZIP exports:

- `assets.pdf`
- `assets.source_md`
- `assets.translated_md[]`
- `assets.summary`
- `assets.summary_templates[]`
- `images[]`

## Product Model

### Output mode

Add an export mode control on the Selected page:

- `ZIP`
- `JSONL`

The current Download ZIP behavior becomes the ZIP mode with configurable included content.

### Content options

#### ZIP mode

ZIP can include:

- Metadata JSON
- PDF
- Source Markdown
- Translated Markdown
- Images
- Selected Summary Templates as JSON

#### JSONL mode

JSONL can include:

- Metadata JSON
- Selected Summary Templates as raw JSON payloads

JSONL should not offer PDF, source markdown, translated markdown, or images, because those are file/binary-oriented assets.

### Summary template picker

Summary templates are shown as individual checkboxes/multi-select items.

Template availability is computed as the union of all templates discovered from selected papers:

1. `detail.summary_urls` keys when detail has been fetched.
2. `item.summary_urls` if present on the selected item.
3. `item.preferred_summary_template` and/or `summary_url` as a fallback default template.

Recommended fallback name for a single `summary_url` without an explicit template is:

```ts
item.preferred_summary_template || detail.preferred_summary_template || 'default'
```

The user can select any subset of discovered templates.

Default initial selection:

- Select the preferred/default template if available.
- If no preferred/default can be inferred, select all discovered templates.
- If no templates are discovered yet, summary export is disabled until paper details are loaded or the user downloads with no summaries.

## UX Flow

1. User opens Selected page.
2. Page initializes selected items as today.
3. Export panel shows:
   - Output mode: ZIP / JSONL
   - Content checkboxes depending on mode
   - Summary template multi-select / checkbox list
4. The page discovers available summary templates lazily:
   - Start from selected item fields.
   - Fetch paper details as needed before export, and optionally before rendering the template picker if the selected items do not expose summary template keys.
5. User clicks Download.
6. Export runs with existing progress/status UI.
7. On completion, user receives the file and a toast.

## JSONL Format

One paper per line. Each line is a standalone JSON object.

Example:

```json
{"paper_id":"abc123","paper_index":12,"title":"Paper title","year":2024,"venue":"Venue","authors":["Author"],"doi":"10.xxxx/yyyy","metadata":{"paper_id":"abc123","title":"Paper title"},"summaries":{"default":{"summary":"...","is_short":false},"deep_read":{"...":"..."}},"missing":{"summaries":["three_pass"]}}
```

Type shape:

```ts
type SelectedPaperJsonlRecord = {
  paper_id: string
  paper_index?: number | string
  title?: string
  year?: number | string
  venue?: string
  authors?: string[]
  doi?: string | null
  metadata?: Record<string, unknown>
  summaries?: Record<string, unknown>
  missing?: {
    metadata?: boolean
    summaries?: string[]
  }
  errors?: Array<{
    kind: 'metadata' | 'summary'
    template?: string
    message: string
  }>
}
```

Rules:

- `metadata` is included only when metadata option is selected.
- `summaries` includes only successfully fetched selected templates.
- `missing.summaries` lists selected templates not available for that paper.
- `errors` records fetch/parse failures without aborting the whole export.
- The file name is `paperdb_selected_<timestamp>.jsonl`.

## ZIP Format

The ZIP keeps the existing per-paper folder structure.

Example:

```text
paperdb_selected_<timestamp>.zip
  <paper-folder>/
    metadata.json
    paper.pdf
    source.md
    translated-zh.md
    summaries/
      default.json
      deep_read.json
    images/...
```

Rules:

- `metadata.json` is written only when metadata is selected.
- Summary templates are exported as raw JSON payloads under `summaries/<template>.json`.
- PDF/source/translated/images are added only when selected.
- File-oriented assets use manifest `static_path` and `zip_path` where available.
- Summary template JSON should prefer `detail.summary_urls` + `getSummaryPayloadCached` over manifest summary markdown assets so ZIP and JSONL summary semantics are consistent.

## Missing Content Behavior

The batch export should be partial-success oriented.

Per paper:

- Missing manifest: skip file-oriented assets; metadata/summary JSON can still be exported if details have URLs.
- Missing selected summary template: record it in JSONL `missing.summaries`; skip it in ZIP.
- Failed summary fetch: record in `errors`; continue.
- Failed binary asset fetch: skip asset and count it as missing/failed; continue.

Batch result:

- If at least one paper produced output, save the file.
- If some content was missing/failed, show a warning toast like `Download completed with N missing items`.
- If no output could be produced, show an error toast and do not save an empty export unless explicitly useful.

## State and Types

Recommended frontend types:

```ts
type SelectedDownloadMode = 'zip' | 'jsonl'

type SelectedDownloadOptions = {
  mode: SelectedDownloadMode
  includeMetadata: boolean
  includePdf: boolean
  includeSourceMarkdown: boolean
  includeTranslatedMarkdown: boolean
  includeImages: boolean
  summaryTemplates: string[]
}

type ResolvedSelectedPaper = {
  item: SearchItem
  detail: PaperDetail | null
  manifest: Manifest | null
  summaryUrls: Record<string, string>
}
```

## Architecture

Refactor Selected page download code into smaller helpers, preferably local to `SelectedView.vue` for v0.10.4 unless it becomes too large:

- `resolvePaperDetail(item)`
- `resolveSummaryUrls(item, detail)`
- `discoverSummaryTemplates(items)`
- `downloadSelectedZip(items, options)`
- `downloadSelectedJsonl(items, options)`
- `writeMetadataJson(folder, detailOrItem)`
- `writeSummaryJson(folderOrRecord, paperId, template, url)`
- `addManifestAsset(folder, baseUrl, asset, overrideZipPath?)`

If `SelectedView.vue` becomes too large, extract to:

```text
frontend/src/lib/selected-export.ts
```

The extraction is recommended because current `SelectedView.vue` already contains multiple responsibilities: selected list rendering, JSON save/load, BibTeX import, and ZIP download.

## Performance Considerations

- Preserve sequential processing for v0.10.4 to avoid overwhelming static/API endpoints.
- Use existing cache for summary payloads.
- Update progress by paper count.
- Avoid prefetching every detail just to render the page if selected count is large; only discover templates on demand or with a lightweight lazy action.
- Respect `MAX_BATCH_SIZE` as today.

## Internationalization

Add English and Chinese strings for:

- Export format
- ZIP package
- JSONL
- Include content
- Metadata JSON
- PDF
- Source Markdown
- Translated Markdown
- Images
- Summary templates
- No summary templates available
- Download completed with missing items
- Download JSONL

## Testing Strategy

Black-box tests should focus on observable behavior:

1. ZIP mode exports only selected asset classes.
2. JSONL mode emits one line per selected paper.
3. JSONL summaries are raw JSON payloads keyed by selected template.
4. Missing templates are recorded per paper.
5. A failed paper/asset does not abort the whole batch.
6. Summary template picker exposes the union of templates across selected papers.
7. JSONL mode disables or hides file-only options.

Tests should avoid asserting internal helper call order.

## Risks

1. **Large SelectedView.vue**
   - Mitigation: extract export helpers to `frontend/src/lib/selected-export.ts`.

2. **Template discovery requires detail fetches**
   - Mitigation: lazy discovery and fallback from existing item fields.

3. **ZIP summary semantics change from markdown asset to JSON payload**
   - Mitigation: document this and keep file-oriented markdown summaries out of v0.10.4 unless explicitly requested.

4. **Partial failures hide data loss**
   - Mitigation: count missing/failed items and surface a warning toast.

5. **Vitest/Vue async state flakiness**
   - Mitigation: tests should wait for visible conditions, not fixed timing only.

## Open Decisions

Resolved by user:

- JSONL summary format: raw JSON payload.
- Summary templates: individual multi-select.
- Version target: v0.10.4.

Still open for implementation review:

- Whether ZIP should additionally include existing manifest markdown summary assets. Recommended: no for v0.10.4; use JSON summaries for consistency.
