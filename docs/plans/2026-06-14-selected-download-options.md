# Selected Download Options Implementation Plan (planned for v0.10.4)

## Goal

Implement configurable Selected-page batch exports for `v0.10.4`:

- Users choose ZIP or JSONL output.
- Users choose which content classes are exported.
- Users select summary templates individually.
- JSONL contains raw JSON summary payloads.
- Exports tolerate missing per-paper content and report partial success.

Design reference: `docs/plans/2026-06-14-selected-download-options-design.md`.

## Dependencies

- Existing Selected page: `frontend/src/views/SelectedView.vue`
- Existing selection store: `frontend/src/stores/selection.ts`
- Existing API helpers: `frontend/src/lib/api.ts`
- Existing lazy helpers: `frontend/src/lib/lazy.ts`
- Existing schemas/types: `frontend/src/types/api.ts`
- Existing i18n: `frontend/src/i18n.ts`
- Existing tests under `frontend/src/__tests__/`

No backend API changes are planned for v0.10.4.

## Implementation Steps

### 1. Add export option types and helper module

**Action**

Create `frontend/src/lib/selected-export.ts`; this is required so the export logic can be tested outside the large `SelectedView.vue` component. `SelectedView.vue` should keep UI state/callback orchestration only. Define:

- `SelectedDownloadMode`
- `SelectedDownloadOptions`
- `SelectedPaperJsonlRecord`
- helper functions for detail resolution, template resolution, JSONL record building, and ZIP file writing.

Suggested helpers:

- `resolvePaperDetail(item)` using `getPaperDetailCached`
- `resolveSummaryUrls(item, detail)` from `PaperDetail.summary_urls` plus `summary_url` fallback
- `discoverSummaryTemplates(items)`
- `buildJsonlRecord(item, detail, selectedTemplates, includeMetadata)`
- `downloadSelectedJsonl(items, options, callbacks)`
- `downloadSelectedZip(items, options, callbacks)`

**Validation**

- TypeScript compiles.
- Helpers can be tested as black-box functions using mocked fetch/API boundaries or dependency injection.

**Estimated effort**

Medium.

### 2. Define export result stats and progress semantics

**Action**

Define a shared stats object used by ZIP and JSONL exports:

- `papersTotal`
- `papersProcessed`
- `filesAdded`
- `jsonlRows`
- `missingAssets`
- `failedAssets`
- `missingSummaries`
- `failedSummaries`
- `metadataFailures`

Progress increments once per selected paper snapshot regardless of success, skip, or error. The partial-success toast uses the sum of missing/failed counters.

**Validation**

- Progress reaches 100% for batches with skipped papers.
- Partial-success messages use the documented counter semantics.
- `filesAdded` counts actual ZIP file entries only; JSONL mode uses `jsonlRows`. Manifest images with non-`available` status increment `missingAssets` without fetch.

**Estimated effort**

Small.

### 3. Refactor current ZIP download logic

**Action**

Move current fixed ZIP logic into a configurable ZIP export path:

- Preserve current folder naming behavior.
- Add `metadata.json` when selected.
- Add PDF/source/translated/images only when selected.
- Add selected summaries using existing manifest assets/zip paths, preserving current ZIP manifest-defined file behavior. Include `manifest.assets.summary` when the selected template set includes the preferred/default template, and include matching `manifest.assets.summary_templates[]` by `template_tag`; de-duplicate identical `zip_path`s. Do not rewrite manifest summary extensions; current snapshots use `summary.json` and `summaries/<template>.json`. ZIP default all-template mode includes all manifest `summary_templates[]` discovered during export even when detail-derived template discovery failed or was stale; manual narrowing applies to matching `template_tag`s only.
- Count skipped/failed assets with the shared export stats object.

**Validation**

- ZIP default options preserve the current fixed Download ZIP behavior: PDF, source markdown, default summary asset, translated markdown, all manifest summary template assets, and images. ZIP mode defaults the summary template picker to all discovered/manifest-backed templates, and export still includes all manifest summary templates in the default all-templates state.
- ZIP output contains only selected asset classes after the user changes defaults.
- Translated markdown preserves the current frontend override path `translated-<lang>.md` for v0.10.4; other manifest-backed assets use manifest `zip_path`.
- Missing manifest does not prevent ZIP metadata export if detail resolution succeeds, but ZIP file assets and manifest-backed summaries are skipped. Metadata-only ZIP entries use `buildFolderName(detail ?? item)` with `item.paper_id` fallback for the folder and single-paper ZIP filename. If detail resolution fails, do not write ZIP `metadata.json`; count `metadataFailures`. JSONL summaries can still use detail summary URLs or item fallback summary URL.

**Estimated effort**

Medium.

### 4. Implement JSONL export

**Action**

Add JSONL export path:

- Iterate selected papers.
- Resolve detail with `getPaperDetailCached` when needed.
- Add `metadata` only when enabled; metadata is the parsed `PaperDetail` object returned by `getPaperDetailCached`, not fetched summary/binary content.
- If detail fetching fails, still emit one JSONL line from `SearchItem` fallback fields. Set `missing.metadata = true` and increment `metadataFailures` only when metadata export was requested. If summary export was requested and `item.summary_url` plus fallback template is available, continue fetching that fallback summary; otherwise mark selected templates missing/failed.
- Fetch selected template payloads via `getSummaryPayloadCached`.
- Emit one JSON string per paper joined by `\n`, plus a final newline.
- Save via `lazySaveAs` as `paperdb_selected_<timestamp>.jsonl`.

**Validation**

- JSONL has exactly one line per selected paper snapshot, including papers whose detail fetch failed; metadata missing flags are emitted only when metadata was requested.
- Each line parses independently as JSON.
- Summary payloads are raw JSON objects keyed by template.
- Missing templates are listed under `missing.summaries`.

**Estimated effort**

Medium.

### 5. Add Selected page export UI

**Action**

In `SelectedView.vue`, add an export options panel near the current download button:

- Output format radio/toggle: ZIP / JSONL.
- Content checkboxes based on mode.
- Summary template checkbox list.
- Download button text changes by mode.
- Disable download when selected count is invalid or no content is selected.

JSONL mode should hide or disable file-only options:

- PDF
- Source Markdown
- Translated Markdown
- Images

**Validation**

- User can select/deselect content options.
- JSONL mode only exposes metadata and summary template options.
- ZIP mode exposes all content options.
- Button labels and disabled state match current state.

**Estimated effort**

Medium.

### 6. Implement summary template discovery

**Action**

Add template discovery for selected papers:

- Use selected item `preferred_summary_template`/`summary_url` as an initial fallback only.
- Fetch details with `getPaperDetailCached` to discover full `summary_urls`; `SearchItem` has no `summary_urls` field.
- Show union of templates as checkbox list.
- Keep selected templates stable when discovery refreshes. ZIP mode auto-selects all discovered templates on first discovery for compatibility; JSONL mode auto-selects preferred/default first, falling back to all templates if no preferred/default can be inferred.
- Disable Download while template discovery is running if summary export is enabled; allow non-summary exports to proceed if summary export is disabled.
- Tie discovery to a selected-items snapshot/revision and discard stale results if selection changes before discovery completes. Export captures its own immutable selected-items snapshot.

**Validation**

- If paper A has `default` and paper B has `deep_read`, UI shows both.
- If selection changes during discovery, the final picker reflects only the latest selection.
- If a selected template is unavailable for a paper, export marks it missing rather than failing.
- Removing all templates disables summary export but still allows metadata-only JSONL/ZIP if metadata is selected.

**Estimated effort**

Medium.

### 7. Add i18n strings

**Action**

Update `frontend/src/i18n.ts` in both English and Chinese for new labels/toasts:

- Export format / 导出格式
- ZIP package / ZIP 包
- JSONL / JSONL
- Include content / 包含内容
- Metadata JSON / 元数据 JSON
- Source Markdown / 原始 Markdown
- Translated Markdown / 翻译 Markdown
- Images / 图片
- Summary templates / 摘要模板
- No summary templates available / 没有可用摘要模板
- Download JSONL / 下载 JSONL
- Download completed with missing items / 下载完成，但有缺失项

**Validation**

- No missing translation keys in UI.
- New export labels/statuses and existing Selected export statuses are covered in both languages, including preparing, fetching manifest, building JSONL, compressing ZIP, ready, failed, partial success, and loading templates.
- English and Chinese Selected page remain understandable.

**Estimated effort**

Small.

### 8. Add black-box tests

**Action**

Add or update frontend tests. Use observable behavior and generated output, not helper internals.

Candidate tests:

1. JSONL mode saves one line per selected paper.
2. JSONL includes raw JSON summaries for selected templates.
3. JSONL records missing templates.
4. ZIP mode includes metadata only when metadata is selected and detail resolution succeeds, including metadata-only ZIP entries when manifest is missing.
5. ZIP mode excludes PDF/images when deselected, preserves manifest-defined summary zip paths/extensions when selected, and preserves translated markdown `translated-<lang>.md` compatibility path.
6. Summary template picker shows union across selected papers.
7. Discovery results from an old selection snapshot are discarded after selection changes.
8. Detail fetch rejection still produces a JSONL row with `missing.metadata`.
9. Progress reaches 100% when papers are skipped or assets are missing.
10. JSONL mode hides/disables file-only options.

Mock or dependency-inject the actual public boundaries used by the export helpers: `getPaperDetailCached`, `getSummaryPayloadCached`, `fetchManifest`, binary `fetch`, `JSZip`, and `saveAs`. Do not mock the obsolete bare `getPaperDetail` path for export tests.

**Validation**

- `cd frontend && npm test -- --run` passes.

**Estimated effort**

Medium to large.

### 9. Run verification

**Action**

Run:

```bash
(cd frontend && npm test -- --run)
(cd frontend && npm run build)
make check

# Security checks to run and report separately:
(cd frontend && npm audit)
npm audit
```

**Validation**

- Frontend tests pass.
- Frontend production build passes.
- Project checks pass.
- npm audit results are reported separately; if implementation changes dependencies or lockfiles, new audit findings introduced by this work must be fixed before release.

**Estimated effort**

Small.

### 10. Version, commit, and tag

**Action**

After implementation and verification:

- Bump relevant package versions from `0.10.3` to `0.10.4`:
  - `pyproject.toml`
  - `uv.lock` project package entry
  - root `package.json` / `package-lock.json`
  - `frontend/package.json` / `frontend/package-lock.json`
- Commit implementation and docs.
- Tag `v0.10.4` at the final commit.

**Validation**

- `git status` clean.
- `git tag --list 'v0.10.*' --sort=-v:refname` shows `v0.10.4`.
- Tag points to final implementation commit.

**Estimated effort**

Small.

## Checkpoints

1. **Design checkpoint**
   - Confirm JSONL raw summary JSON shape.
   - Confirm ZIP summaries preserve current manifest-defined file assets for compatibility.

2. **UI checkpoint**
   - Confirm Selected page export panel layout is acceptable.

3. **Behavior checkpoint**
   - Confirm missing content policy and partial-success toast behavior.

4. **Release checkpoint**
   - Confirm bump/tag/push timing for `v0.10.4`.

## Risks and Mitigations

### Risk: SelectedView becomes too large

Mitigation: extract export logic into `frontend/src/lib/selected-export.ts`.

### Risk: template discovery is slow for many selected papers

Mitigation: lazy discovery, sequential detail fetches, progress/status text.

### Risk: JSONL output becomes too large

Mitigation: keep raw JSON payloads only for explicitly selected templates.

### Risk: partial failures go unnoticed

Mitigation: include missing/error fields in JSONL and show warning toast with count.

### Risk: tests become flaky due async Vue state

Mitigation: wait for visible UI conditions and saved output rather than fixed timing.

## Proposed Commit Structure

1. `docs: design selected export options`
2. `feat: add selected export options`
3. `chore: bump version to 0.10.4`

If the implementation is compact, commits 1 and 2 may be combined, but version bump should preferably remain separate.
