# Frontend Paper Content Cache Design

## Context

The frontend currently reloads the same paper detail content repeatedly:

- `getPaperDetail(...)` always fetches metadata from the server
- summary JSON is fetched again when a user revisits the same summary template
- translated markdown is fetched again when a user revisits the same translation
- markdown and rendered HTML already have small in-memory caches, but those caches disappear on refresh and do not operate at the paper level

This is most noticeable in the paper detail view, where users often bounce between:

- the same paper across multiple sessions
- multiple summary templates for one paper
- translated markdown and summary for one paper

The goal is not offline support. The goal is faster repeat opens for papers a user has already read recently.

## Requirements

- Cache paper detail metadata, summary content, and translated markdown in the frontend.
- Use a paper-level cache unit, not three unrelated per-URL caches.
- Optimize for repeat-open speed, not offline-first behavior.
- Keep the implementation compatible with the current frontend data flow.
- Avoid silently swapping long-form summary or translation content while a user is reading.
- Bound storage with a clear LRU policy.
- Make cache schema evolution safe.

## Non-Goals

- Caching PDFs.
- Building a full offline mode.
- Rewriting the existing search result fetch pipeline.
- Adding backend API fields such as `updated_at` or `etag` in the first iteration.
- Caching every paper the user ever opened indefinitely.

## Recommended Approach

Use IndexedDB as the persistent cache, with a small in-memory hot cache on top.

The cache unit should be one record per `paper_id`, containing:

- detail metadata
- cached summaries keyed by template
- cached translated markdown keyed by language

This matches the actual user task: “open a paper again and have the important reading surfaces appear immediately.”

## Freshness Model

### Why not use timestamps

The current frontend `PaperDetail` payload does not expose `updated_at`, `etag`, or any similar freshness field.

That means the first iteration cannot rely on a server-provided timestamp.

### What the current backend already provides

Snapshot-backed asset URLs already carry freshness signals:

- `summary_url` and `summary_urls[tag]` append `?v=<snapshot_build_id>`
- translated markdown URLs include content-hash-derived paths

This is not just a frontend assumption. The current snapshot API constructs these URLs in:

- `python/deepresearch_flow/paper/snapshot/api.py:_asset_urls(...)`
- `python/deepresearch_flow/paper/snapshot/api.py:_summary_urls(...)`

Specifically, the current implementation appends `?v=<snapshot_build_id>` to summary and manifest URLs at:

- `python/deepresearch_flow/paper/snapshot/api.py:127-131`
- `python/deepresearch_flow/paper/snapshot/api.py:156-158`

So the first iteration will treat the resource identity itself as the freshness key.

### Chosen freshness key

For each cached paper:

- detail freshness is derived from the set of current asset URLs returned by `getPaperDetail(...)`
- summary freshness is the full summary URL for that template
- translation freshness is the full translated markdown URL for that language

If the URL identity changes, that cached sub-entry is stale.

This avoids inventing a pseudo-timestamp and fits the current backend reality.

### Comparison rules

Some freshness fields are record-typed:

- `summaryUrls: Record<string, string>`
- `translatedMdUrls: Record<string, string>`

Freshness comparison for these fields must be order-independent.

That means the implementation must not rely on raw `JSON.stringify(...)` of unsorted objects. It should either:

- compare sorted key/value pairs
- or perform an explicit deep equality check that ignores key insertion order

## Read and Revalidation Behavior

### Detail metadata

Detail metadata may use:

- cache first for instant paint
- then background revalidation
- then silent cache refresh

This is safe because metadata changes are usually small and do not disrupt long-form reading position.

### Summary and translation

Summary and translated markdown will **not** use background silent replacement.

Instead:

- if cached content exists for the requested template or language, use it immediately
- only check freshness when the user explicitly opens that template or language
- if the resource identity changed, fetch the new content and replace the cached entry before rendering that newly requested view

This prevents the “I was reading paragraph three and the content changed underneath me” problem.

If a paper record already exists but a requested summary template or translation language does not yet exist inside that record, that is a normal cache miss for that sub-entry. The frontend should fetch it, render it, and write it back into the existing paper record.

## Cache Shape

Each record is stored by `paper_id` and must include a schema version:

```ts
{
  schemaVersion: 1,
  paperId: string,
  detail: {
    payload: PaperDetail,
    freshness: {
      manifestUrl: string,
      summaryUrl: string,
      summaryUrls: Record<string, string>,
      translatedMdUrls: Record<string, string>,
      sourceMdUrl: string | null,
    },
    cachedAt: number,
  } | null,
  summaries: {
    [template: string]: {
      url: string,
      payload: Record<string, unknown>,
      cachedAt: number,
    }
  },
  translations: {
    [lang: string]: {
      url: string,
      markdown: string,
      cachedAt: number,
    }
  },
  lastAccessedAt: number,
}
```

If `schemaVersion` is not supported, the record is treated as a miss.

## Storage Policy

### Persistent cache

Use IndexedDB as the persistent store because:

- content can be non-trivial in size
- cached data should survive refresh and browser restart
- the repo already uses IndexedDB patterns for token persistence

### In-memory hot cache

Use a tiny in-memory cache for only:

- current paper
- immediately previous paper

That means an in-memory hot cache size of `2`.

Anything larger adds complexity without much value once IndexedDB exists underneath.

## LRU Policy

The eviction unit is one whole paper record.

First iteration policy:

- keep at most `50` papers
- evict the least-recently-accessed paper when the limit is exceeded

`50` is intentionally above the user’s minimum requirement of “at least 10” and is still conservative for IndexedDB.

### Touch behavior

Only update `lastAccessedAt` when the user opens or navigates to a paper detail view for that `paper_id`.

Do **not** update `lastAccessedAt` when the user repeatedly switches summary templates or translation languages inside the same paper.

This prevents one heavily inspected paper from constantly pinning itself at the top of the LRU just because the user toggled views.

Cache writes triggered by background detail revalidation must also **not** update `lastAccessedAt`. Revalidation is a maintenance write, not a user access event.

### Transaction rule

LRU enforcement must happen inside the same IndexedDB `readwrite` transaction as the write that may cause the cache to exceed its limit.

The implementation must not split:

- read current count
- write the new paper record
- evict the oldest paper

into separate transactions, because that can over-evict under concurrent multi-tab writes.

## Integration Points

Create one shared frontend cache module, for example:

- `frontend/src/lib/paper-content-cache.ts`

This module should own:

- IndexedDB open/create logic
- schema-version checks
- paper-level record reads and writes
- LRU eviction
- in-memory hot cache

The rest of the app should use wrappers rather than touching IndexedDB directly.

Recommended wrapper surface:

- `getPaperDetailCached(paperId)`
- `getSummaryCached(paperId, template, url)`
- `getTranslationCached(paperId, lang, url)`
- `touchPaperCache(paperId)`

## Write Timing

Write to cache immediately after a successful fetch.

Do not wait for render completion.

Reasons:

- simpler control flow
- fewer lost opportunities when users navigate quickly
- easier black-box testing

## Compatibility With Existing UI

The first iteration should keep the UI behavior nearly unchanged:

- detail view still renders the same components
- summary template switching still works the same way
- translation switching still works the same way
- no new visible “cached” badges are required

The improvement is latency, not a new visible workflow.

## Testing Strategy

Tests should stay black-box and focus on observable behavior:

- cached detail is reused on repeated open
- cached summary is reused on repeated template open
- cached translated markdown is reused on repeated translation open
- first-time open of an uncached summary template fetches from the network and writes back into an existing paper record
- first-time open of an uncached translation language fetches from the network and writes back into an existing paper record
- opening more than 50 papers evicts the oldest paper record
- schema-version mismatch is treated as cache miss
- detail freshness proxy change invalidates stale detail cache
- summary cache is replaced when the template URL changes
- translation cache is replaced when the language URL changes
- repeated template toggling within one paper does not retouch LRU for other papers
- background detail revalidation that writes newer metadata does not change the LRU position of that paper

## Success Criteria

- reopening a recently viewed paper shows detail content faster
- reopening a previously viewed summary template avoids a redundant fetch when the resource identity is unchanged
- reopening a previously viewed translation avoids a redundant fetch when the resource identity is unchanged
- cached data remains bounded to 50 papers
- stale schema versions fail safely
- users do not experience mid-read silent summary or translation replacement
