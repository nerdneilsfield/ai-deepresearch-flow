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

Create `frontend/src/lib/selected-export.ts`; this is required so the export logic can be tested outside the large `SelectedView.vue` component. Define:

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

### 2. Refactor current ZIP download logic

**Action**

Move current fixed ZIP logic into a configurable ZIP export path:

- Preserve current folder naming behavior.
- Add `metadata.json` when selected.
- Add PDF/source/translated/images only when selected.
- Add selected summaries using existing manifest assets/zip paths, preserving current ZIP markdown/file behavior.
- Count skipped/failed assets.

**Validation**

- Existing default ZIP behavior can be approximated by selecting all file content options plus summary templates; summary files remain manifest-backed assets, not raw JSON payloads.
- ZIP output contains only selected asset classes.
- Missing manifest does not prevent metadata/summary export.

**Estimated effort**

Medium.

### 3. Implement JSONL export

**Action**

Add JSONL export path:

- Iterate selected papers.
- Resolve detail with `getPaperDetailCached` when needed.
- Add `metadata` only when enabled; metadata is the parsed `PaperDetail` object returned by `getPaperDetailCached`, not fetched summary/binary content.
- Fetch selected template payloads via `getSummaryPayloadCached`.
- Emit one JSON string per paper joined by `\n`, plus a final newline.
- Save via `lazySaveAs` as `paperdb_selected_<timestamp>.jsonl`.

**Validation**

- JSONL has exactly one line per selected paper.
- Each line parses independently as JSON.
- Summary payloads are raw JSON objects keyed by template.
- Missing templates are listed under `missing.summaries`.

**Estimated effort**

Medium.

### 4. Add Selected page export UI

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

### 5. Implement summary template discovery

**Action**

Add template discovery for selected papers:

- Use selected item `preferred_summary_template`/`summary_url` as an initial fallback only.
- Fetch details with `getPaperDetailCached` to discover full `summary_urls`; `SearchItem` has no `summary_urls` field.
- Show union of templates as checkbox list.
- Keep selected templates stable when discovery refreshes; auto-select preferred/default on first discovery.
- Disable Download while template discovery is running if summary export is enabled; allow non-summary exports to proceed if summary export is disabled.

**Validation**

- If paper A has `default` and paper B has `deep_read`, UI shows both.
- If a selected template is unavailable for a paper, export marks it missing rather than failing.
- Removing all templates disables summary export but still allows metadata-only JSONL/ZIP if metadata is selected.

**Estimated effort**

Medium.

### 6. Add i18n strings

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
- English and Chinese Selected page remain understandable.

**Estimated effort**

Small.

### 7. Add black-box tests

**Action**

Add or update frontend tests. Use observable behavior and generated output, not helper internals.

Candidate tests:

1. JSONL mode saves one line per selected paper.
2. JSONL includes raw JSON summaries for selected templates.
3. JSONL records missing templates.
4. ZIP mode includes metadata only when metadata is selected.
5. ZIP mode excludes PDF/images when deselected.
6. Summary template picker shows union across selected papers.
7. JSONL mode hides/disables file-only options.

Mock `fetch`, `getPaperDetail`, summary payload fetches, `JSZip`, and `saveAs` only at public boundaries.

**Validation**

- `cd frontend && npm test -- --run` passes.

**Estimated effort**

Medium to large.

### 8. Run verification

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

### 9. Version, commit, and tag

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
   - Confirm ZIP summaries preserve current manifest markdown/file assets for compatibility.

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
