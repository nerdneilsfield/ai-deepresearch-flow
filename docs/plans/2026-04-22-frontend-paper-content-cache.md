# Frontend Paper Content Cache Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cache recent paper detail metadata, summaries, and translated markdown in the frontend so repeat opens are faster across sessions.

**Architecture:** Add one paper-level IndexedDB cache module with schema-versioned records, a `50`-paper LRU, and a tiny two-entry in-memory hot cache. Detail metadata uses cache-first plus background revalidation, while summary and translation content are only refreshed when the user explicitly opens that template or language and the resource identity has changed.

**Tech Stack:** Vue 3, TypeScript, IndexedDB, existing frontend API wrappers, Vitest.

---

### Task 1: Add a paper-level cache module and storage tests

**Files:**
- Create: `frontend/src/lib/paper-content-cache.ts`
- Test: `frontend/src/__tests__/paperContentCache.test.ts`

**Step 1: Write the failing tests**

Add black-box tests covering:
- a paper detail record can be written and read back by `paper_id`
- schema-version mismatch is treated as cache miss
- the cache keeps at most `50` paper records
- the oldest paper record is evicted when the limit is exceeded
- touching one paper updates access time only for that paper

**Step 2: Run tests to verify they fail**

Run:

```bash
cd frontend && npm test -- --run src/__tests__/paperContentCache.test.ts
```

Expected: FAIL because the cache module does not exist yet.

**Step 3: Write minimal implementation**

Implement `paper-content-cache.ts` with:
- IndexedDB open logic
- one object store for paper cache records
- `schemaVersion: 1`
- read/write helpers
- `touchPaper(...)`
- `enforcePaperLimit(...)`
- a two-entry in-memory hot cache

**Step 4: Run tests to verify they pass**

Run the same command again and confirm PASS.

**Step 5: Commit implementation and tests separately**

```bash
git add frontend/src/lib/paper-content-cache.ts
git commit -m "feat(frontend): add paper content cache store"

git add frontend/src/__tests__/paperContentCache.test.ts
git commit -m "test(frontend): cover paper content cache storage"
```

### Task 2: Cache paper detail metadata

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/paper-content-cache.ts`
- Modify: `frontend/src/views/PaperDetailView.vue`
- Test: `frontend/src/__tests__/usePaperDetail.test.ts`
- Test: `frontend/src/__tests__/paperContentCache.test.ts`

**Step 1: Write the failing tests**

Add black-box tests covering:
- repeated paper detail open reuses cached detail payload
- detail cache becomes stale when the returned freshness proxy changes
- opening a paper touches that paper once in the LRU

**Step 2: Run tests to verify they fail**

Run:

```bash
cd frontend && npm test -- --run src/__tests__/usePaperDetail.test.ts src/__tests__/paperContentCache.test.ts
```

Expected: FAIL because detail fetches do not use the cache.

**Step 3: Write minimal implementation**

Add a cached detail wrapper that:
- checks in-memory hot cache first
- falls back to IndexedDB
- returns cached detail immediately when present
- performs background revalidation using the current `getPaperDetail(...)`
- updates cache if the detail freshness proxy changed

Use these fields for the detail freshness proxy:
- `manifest_url`
- `summary_url`
- `summary_urls`
- `translated_md_urls`
- `source_md_url`

**Step 4: Run tests to verify they pass**

Run the same test command again and confirm PASS.

**Step 5: Commit implementation and tests separately**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/paper-content-cache.ts frontend/src/views/PaperDetailView.vue
git commit -m "feat(frontend): cache paper detail metadata"

git add frontend/src/__tests__/usePaperDetail.test.ts frontend/src/__tests__/paperContentCache.test.ts
git commit -m "test(frontend): cover cached paper detail loading"
```

### Task 3: Cache summaries by template

**Files:**
- Modify: `frontend/src/lib/paper-content-cache.ts`
- Modify: `frontend/src/views/PaperDetailView.vue`
- Modify: `frontend/src/composables/useExpandableSummary.ts`
- Test: `frontend/src/__tests__/paperContentCache.test.ts`
- Test: `frontend/src/__tests__/PaperDetailViewAdvancedContext.test.ts`

**Step 1: Write the failing tests**

Add black-box tests covering:
- reopening the same summary template reuses cached summary payload
- changing the summary URL for a template invalidates the cached summary entry
- summary content is not silently background-swapped while already displayed

**Step 2: Run tests to verify they fail**

Run:

```bash
cd frontend && npm test -- --run src/__tests__/paperContentCache.test.ts src/__tests__/PaperDetailViewAdvancedContext.test.ts
```

Expected: FAIL because summary fetches still go straight to the network path.

**Step 3: Write minimal implementation**

Add a cached summary wrapper that:
- stores summary payloads under `paper_id + template`
- keys freshness on the full summary URL
- uses cache-first for the currently requested template
- only fetches the network version when the requested template URL differs or no cache exists

Update:
- detail view summary loading path
- expandable summary loading path on search results

**Step 4: Run tests to verify they pass**

Run the same command again and confirm PASS.

**Step 5: Commit implementation and tests separately**

```bash
git add frontend/src/lib/paper-content-cache.ts frontend/src/views/PaperDetailView.vue frontend/src/composables/useExpandableSummary.ts
git commit -m "feat(frontend): cache paper summaries"

git add frontend/src/__tests__/paperContentCache.test.ts frontend/src/__tests__/PaperDetailViewAdvancedContext.test.ts
git commit -m "test(frontend): cover cached paper summaries"
```

### Task 4: Cache translated markdown by language

**Files:**
- Modify: `frontend/src/lib/paper-content-cache.ts`
- Modify: `frontend/src/components/MarkdownPanel.vue`
- Test: `frontend/src/__tests__/paperContentCache.test.ts`
- Test: `frontend/src/__tests__/useSplitView.test.ts`

**Step 1: Write the failing tests**

Add black-box tests covering:
- reopening the same translated markdown reuses cached content
- changing the translated markdown URL invalidates the cached translation entry
- switching translations within one paper does not retouch the global LRU timestamp for unrelated papers

**Step 2: Run tests to verify they fail**

Run:

```bash
cd frontend && npm test -- --run src/__tests__/paperContentCache.test.ts src/__tests__/useSplitView.test.ts
```

Expected: FAIL because translated markdown still relies only on the transient in-memory panel cache.

**Step 3: Write minimal implementation**

Add a cached translation wrapper that:
- stores markdown under `paper_id + lang`
- uses the full translated markdown URL as freshness key
- returns cached markdown immediately when the requested URL matches
- only fetches and rewrites cache when the requested translation URL differs or no cache exists

Update `MarkdownPanel.vue` so translation loads use the shared cache module instead of their local-only cache.

**Step 4: Run tests to verify they pass**

Run the same command again and confirm PASS.

**Step 5: Commit implementation and tests separately**

```bash
git add frontend/src/lib/paper-content-cache.ts frontend/src/components/MarkdownPanel.vue
git commit -m "feat(frontend): cache translated markdown"

git add frontend/src/__tests__/paperContentCache.test.ts frontend/src/__tests__/useSplitView.test.ts
git commit -m "test(frontend): cover cached translated markdown"
```

### Task 5: Run focused frontend verification

**Files:**
- Verify only

**Step 1: Run focused tests**

Run:

```bash
cd frontend && npm test -- --run \
  src/__tests__/paperContentCache.test.ts \
  src/__tests__/usePaperDetail.test.ts \
  src/__tests__/PaperDetailViewAdvancedContext.test.ts \
  src/__tests__/useSplitView.test.ts \
  src/__tests__/HelpView.test.ts
```

Expected: PASS

**Step 2: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS, allowing the existing non-blocking chunk-size warnings.

**Step 3: Run one manual smoke check**

Verify in browser:
- open one paper detail page
- switch summary template twice
- switch translated markdown twice
- reload the page
- reopen the same paper and confirm summary/translation render faster than first open

**Step 4: Commit any residual cleanup**

If any non-functional cleanup remains after verification, commit it separately with a narrow message.
