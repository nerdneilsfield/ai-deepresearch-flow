# Selected Download Options Design (planned for v0.10.4)

## Background

The Selected page currently downloads every selected paper as a ZIP with a fixed set of assets: PDF, source markdown, default summary, translated markdown, all summary template assets, and images when present in the manifest.

For research workflows this is too coarse. Sometimes the user only wants metadata and selected summary templates for downstream agent/LLM processing. In that case a ZIP of many files is inconvenient; a JSONL file with one paper per line is easier to feed into tools.

This feature is planned for `v0.10.4`; the current released/project version may still be earlier until implementation and release tagging are complete.

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

`getPaperDetailCached(paperId)` should be used by the export path to share the same IndexedDB-backed freshness behavior as the detail page. It returns richer `PaperDetail`, including:

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
- Selected Summary Templates as manifest-defined files (currently JSON summary assets from the snapshot builder)

ZIP mode defaults should preserve the current `Download ZIP` behavior for compatibility: PDF, source markdown, default summary asset, translated markdown, all manifest summary template assets, and images are selected by default when available. In ZIP mode, the summary template picker therefore defaults to all discovered/manifest-backed templates, not only the preferred/default template. Users can opt out of individual content classes or templates.

The manifest default summary asset (`manifest.assets.summary`) and template assets (`manifest.assets.summary_templates[]`) are distinct sources. When the selected template set includes the preferred/default template, ZIP export should include `manifest.assets.summary`. It should also include matching `summary_templates[]` assets by `template_tag`. If both sources resolve to the same `zip_path`, add the file only once. Do not override manifest `zip_path` extensions; current snapshots use `summary.json` and `summaries/<template>.json`. ZIP compatibility must not depend solely on detail-derived `summary_urls`: when ZIP summary export is enabled and the user has not manually narrowed templates, include all manifest `summary_templates[]` assets discovered during export, even if detail/template discovery failed or was stale. Manual template narrowing applies only to templates that can be matched by `template_tag`; unmatched manifest templates are included in the default all-templates ZIP mode.

#### JSONL mode

JSONL can include:

- Metadata JSON
- Selected Summary Templates as raw JSON payloads

JSONL should not offer PDF, source markdown, translated markdown, or images, because those are file/binary-oriented assets.

### Summary template picker

Summary templates are shown as individual checkboxes/multi-select items.

Template availability is computed as the union of all templates discovered from selected papers:

1. `detail.summary_urls` keys after fetching/caching `PaperDetail` with `getPaperDetailCached`.
2. `item.preferred_summary_template` and/or `item.summary_url` as a fallback default template before detail discovery completes.

`SearchItem` does not currently contain `summary_urls`; full per-template discovery therefore requires fetching paper details. No backend/search-result schema change is planned for v0.10.4.

Recommended fallback name for a single `summary_url` without an explicit template is:

```ts
item.preferred_summary_template || detail.preferred_summary_template || 'default'
```

The user can select any subset of discovered templates.

Default initial selection:

- ZIP mode: select all discovered templates by default to preserve the current Download ZIP behavior.
- JSONL mode: select the preferred/default template if available.
- JSONL mode: if no preferred/default can be inferred, select all discovered templates.
- While template discovery is running, show a `Loading templates…` state. If summary export is enabled, disable the Download button until discovery completes; if summary export is disabled, allow metadata/file-only downloads to proceed.
- If no templates are discovered after detail loading, summary export remains disabled and the user can still download non-summary content.

Template discovery must be tied to a selected-items snapshot/revision. If the selection changes while discovery is running, stale discovery results are discarded and discovery restarts for the latest snapshot. Export actions also capture their own item snapshot so progress, available templates, and output rows/files refer to a consistent batch.

## UX Flow

1. User opens Selected page.
2. Page initializes selected items from the selection store.
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
- JSONL emits exactly one line for every selected paper snapshot. If detail fetching fails, emit a minimal record from the `SearchItem` fallback fields. Set `missing.metadata = true` and increment `metadataFailures` only when metadata export was requested. If summary export was requested and `item.summary_url` plus fallback template is available, still fetch that summary; otherwise mark the selected templates unavailable/failed as appropriate.
- The file name is always `paperdb_selected_<timestamp>.jsonl`, including single-paper exports. ZIP keeps its existing single-paper folder-name behavior for compatibility.


## Metadata JSON Shape

When metadata export is enabled and detail resolution succeeds, both ZIP `metadata.json` and JSONL `metadata` use the same object shape: the JSON-serializable `PaperDetail` returned by `getPaperDetailCached`, after schema parsing. This intentionally includes useful URL fields such as `summary_url`, `summary_urls`, `manifest_url`, PDF/markdown URLs, and facet metadata. It does not inline fetched binary files or fetched summary payloads; summaries live under the separate `summaries` field/path. If detail resolution fails, do not write ZIP `metadata.json`; increment `metadataFailures`. JSONL still emits the minimal fallback row described above, but omits `metadata`.

Top-level convenience fields in the JSONL record (`paper_id`, `title`, `year`, `venue`, `authors`, `doi`) duplicate common metadata for stream processing, but `metadata` remains the authoritative full detail object.

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
      deep_read.json
    summary.json
    images/...
```

Rules:

- `metadata.json` is written only when metadata is selected.
- PDF/source/translated/images are added only when selected.
- PDF/source/images and summary assets use manifest `static_path` and `zip_path` where available. Translated markdown is the compatibility exception: preserve the current frontend path override `translated-<lang>.md` for v0.10.4 rather than switching to manifest `translated/<lang>.md`.
- ZIP-mode summary templates preserve the current manifest asset behavior, including manifest-defined file names/extensions. Current snapshots use JSON summary files (`summary.json`, `summaries/<template>.json`), and the frontend must not hard-code `.md` or rewrite these paths.
- Raw JSON summary payloads are used for JSONL mode only in v0.10.4. A future explicit option can add JSON summaries to ZIP if needed.

## Export Result Statistics

Export helpers should return a stable stats object so UI messages and tests use the same meaning:

```ts
type SelectedExportStats = {
  papersTotal: number
  papersProcessed: number
  filesAdded: number
  jsonlRows: number
  missingAssets: number
  failedAssets: number
  missingSummaries: number
  failedSummaries: number
  metadataFailures: number
}
```

Progress advances once per selected paper snapshot regardless of success, skip, or error, and should reach 100% when the batch loop finishes. The partial-success toast should use the sum of `missingAssets + failedAssets + missingSummaries + failedSummaries + metadataFailures` as `N`. `filesAdded` counts actual file entries written to a ZIP, including `metadata.json`, but not folder entries, skipped/de-duplicated assets, missing assets, or the JSONL file itself. In JSONL mode, `filesAdded` remains 0 and `jsonlRows` reports emitted rows. Manifest image entries with `status` present and not `available` count as `missingAssets` and should not be fetched.

## Missing Content Behavior

The batch export should be partial-success oriented.

Per paper:

- Missing manifest: ZIP skips manifest-backed file assets and manifest-backed summaries, but can still write `metadata.json` when metadata is selected and detail resolution succeeds. In that metadata-only ZIP case, create the per-paper folder from `buildFolderName(detail ?? item)` with `item.paper_id` fallback; for a single-paper ZIP, use the same fallback folder name for the ZIP filename. If detail resolution fails, do not write ZIP `metadata.json`; count `metadataFailures`. JSONL metadata and summaries can still be exported if details or item fallback fields have summary URLs.
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

Refactor Selected page download code into `frontend/src/lib/selected-export.ts` for v0.10.4 so export behavior can be black-box tested outside the large `SelectedView.vue` component. `SelectedView.vue` should own UI state and callbacks only. The helper module should expose:

- `resolvePaperDetail(item)`
- `resolveSummaryUrls(item, detail)`
- `discoverSummaryTemplates(items)`
- `downloadSelectedZip(items, options)`
- `downloadSelectedJsonl(items, options)`
- `writeMetadataJson(folder, detail)`; only writes parsed `PaperDetail`, never a `SearchItem` fallback
- `writeJsonlSummaryPayload(record, paperId, template, url)` for JSONL raw summary payloads
- `addManifestAsset(folder, baseUrl, asset, overrideZipPath?)`

ZIP asset URL base resolution should use the existing `resolveStaticBaseUrl` helper from `frontend/src/lib/static-base.ts`, with candidates such as `manifestUrl`, `detail?.manifest_url`, `item.manifest_url`, and relevant asset/detail URLs. Do not duplicate the old SelectedView-only `/manifest/` parser.

This extraction is required for v0.10.4 because current `SelectedView.vue` already contains multiple responsibilities and the export behavior needs black-box helper tests.

## Performance Considerations

- Preserve sequential processing for v0.10.4 to avoid overwhelming static/API endpoints.
- Use existing cache for paper details and summary payloads (`getPaperDetailCached`, `getSummaryPayloadCached`).
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
- Loading templates…
- Preparing download…
- Fetching manifest…
- Building JSONL…
- Compressing ZIP…
- Download ready
- Download failed

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

3. **ZIP summary compatibility**
   - Mitigation: preserve current manifest-based ZIP summary assets and manifest-defined paths/extensions in v0.10.4; JSON summary payload fetching is limited to JSONL mode.

4. **Partial failures hide data loss**
   - Mitigation: count missing/failed items and surface a warning toast.

5. **Vitest/Vue async state flakiness**
   - Mitigation: tests should wait for visible conditions, not fixed timing only.

## Decisions

Resolved by user:

- JSONL summary format: raw JSON payload.
- Summary templates: individual multi-select.
- Version target: v0.10.4.

Implementation decisions for v0.10.4:

- ZIP summary output preserves existing manifest asset behavior and manifest-defined paths/extensions.
- JSONL summary output uses raw JSON payloads.
- Full template discovery requires `getPaperDetailCached` because `SearchItem` has no `summary_urls` field.
