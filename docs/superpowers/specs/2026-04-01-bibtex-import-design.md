# BibTeX Import for Selection Page

**Date:** 2026-04-01
**Status:** Draft

## Problem

The selection page currently only supports importing paper lists via JSON (`paper_id` + `title`). Users who have a `.bib` file (e.g., exported from Zotero, Mendeley, or a LaTeX project) cannot use it to batch-add papers to the selection. They must manually search and select each paper.

## Solution Overview

Add a "Import BibTeX" button to the selection page. The user uploads a `.bib` file, the frontend splits it into batches of raw BibTeX text (50 entries per batch), and POSTs each batch to a new backend API that parses and matches entries against the paper database. Matched papers are added to the selection list; unmatched entries are displayed with a link to search manually.

## Architecture

```
User uploads .bib file
       ↓
Frontend: read file text, validate non-empty
       ↓
Frontend: split by @-entry boundaries into entry list
       ↓
Frontend: chunk into batches of 50 entries (raw text)
       ↓
POST /api/v1/papers/match-bibtex  (per batch)
       ↓
Backend: pybtex parse → extract title, DOI per entry
       ↓
Backend: two-level matching per entry:
  1. DOI exact match (canonicalize_doi → query paper.doi)
  2. Title fuzzy match (unique best candidate, SequenceMatcher ≥ 0.9)
       ↓
Backend: return { matched, unmatched, stats }
       ↓
Frontend: accumulate results across batches
  - matched → add to selection (append or replace)
  - unmatched → display with "Search" links (open in new tab)
  - failed batches → track and report in toast
```

## Backend API

### `POST /api/v1/papers/match-bibtex`

**Request:**

```json
{
  "bibtex_raw": "@article{key1, title={...}, doi={...}}\n@inproceedings{key2, ...}"
}
```

- `bibtex_raw` (string, required): Raw BibTeX text containing up to 50 entries.

**Response:**

```json
{
  "matched": [
    {
      "bibtex_key": "key1",
      "paper_id": "abc123",
      "match_method": "doi",
      "title": "Graph Neural Networks...",
      "year": "2023",
      "venue": "NeurIPS",
      "authors": ["Alice Smith", "Bob Jones"]
    }
  ],
  "unmatched": [
    {
      "bibtex_key": "key2",
      "title": "Some Paper Not In DB",
      "search_query": "Some Paper Not In DB"
    }
  ],
  "stats": {
    "total": 50,
    "matched": 48,
    "unmatched": 2
  }
}
```

**`matched` item fields:**

| Field | Type | Description |
|-------|------|-------------|
| `bibtex_key` | string | Original BibTeX citation key |
| `paper_id` | string | Matched paper ID in database |
| `match_method` | `"doi"` \| `"title"` | Which matching level succeeded |
| `title` | string | Paper title from database |
| `year` | string \| null | Publication year |
| `venue` | string \| null | Venue name |
| `authors` | string[] | Author list |

> The backend returns enough metadata so the frontend can construct a `SearchItem` directly without calling `getPaperDetail()` for each match. This avoids N+1 API calls. Fields not returned (`summary_preview`, `has_pdf`, `manifest_url`, etc.) are optional in `SearchItemSchema` and can be omitted; the frontend treats them as `undefined`.

**`unmatched` item fields:**

| Field | Type | Description |
|-------|------|-------------|
| `bibtex_key` | string | Original BibTeX citation key |
| `title` | string \| null | Title extracted from BibTeX entry |
| `search_query` | string | Suggested search query for manual lookup |

### Matching Logic (Backend)

Per entry, executed in order — stop at first match:

1. **DOI exact match:**
   - Extract DOI from BibTeX entry via `extract_doi_from_bibtex_raw()`
   - Canonicalize with `canonicalize_doi()`
   - Query: `SELECT paper_id, title, year, venue FROM paper WHERE doi = ?`

2. **Title fuzzy match (strict uniqueness):**
   - Extract title from parsed BibTeX entry, normalize (lowercase, strip punctuation)
   - Build prefix index (first 16 chars) over all paper titles for acceleration (same strategy as `enrich_with_bibtex` in `db_ops.py`)
   - Collect all candidates with `difflib.SequenceMatcher` ratio ≥ 0.9
   - **Uniqueness rule:** Only accept if the best candidate's score leads the second-best by ≥ 0.05. If two or more candidates score within 0.05 of each other, treat as ambiguous → unmatched.
   - **Year cross-check:** If the BibTeX entry has a `year` field and the best candidate's year differs, treat as unmatched (same title, different edition/version).

3. **No match → unmatched list**

### Implementation Notes

- Reuse `canonicalize_doi()` from `identity.py`
- Reuse title normalization and prefix-index strategy from `db_ops.py:enrich_with_bibtex()`
- Parse with `pybtex` (already a project dependency)
- The prefix index is built once per request and reused across all entries in the batch
- Error handling: if `pybtex` fails to parse an entry, include it in `unmatched` with `title: null` and `search_query` set to the bibtex_key
- Authors are fetched via JOIN on `paper_author` table for matched papers

## Frontend Changes

All changes are in `SelectedView.vue` and `api.ts`. No new Vue components needed.

### UI Elements

1. **"Import BibTeX" button** — next to existing "Load List" button, `accept=".bib"`
2. **Mode popover** — on click, small popover with two options: "Append" (default) / "Replace"
3. **Progress bar** — reuse existing `Progress` component, show batch progress ("Matching 50/150...")
4. **Unmatched panel** — collapsible warning banner above the paper list:
   ```
   ⚠ 3 papers not found in database
   ├ "Some Paper Title"        [Search →]  (opens new tab)
   ├ "Another Paper"           [Search →]  (opens new tab)
   └ "Third One"               [Search →]  (opens new tab)
   ```
   - "Search →" opens `/?q=<search_query>` in a **new browser tab** (`target="_blank"`)
   - Does NOT navigate away from the current selection page
5. **Toast** — on completion: "Matched X, not found Y, failed Z" (failed count only shown if > 0)

### Frontend Logic

1. User clicks "Import BibTeX" → file picker (`.bib`)
2. Read file as text; if empty or unreadable → toast error, abort
3. Split into entries by `@` boundary regex: `/@(?=\w+\s*\{)/g`
4. If zero entries found → toast error, abort
5. Chunk into batches of 50 entries, rejoin each batch as raw text
6. For each batch:
   - `POST /api/v1/papers/match-bibtex` with `{ bibtex_raw }`
   - On success: accumulate `matched` and `unmatched` items into staging lists
   - On failure: record batch as failed, continue with remaining batches
   - Update progress bar
7. After all batches complete:
   - If any batch failed → toast error "N entries could not be processed", do NOT apply Replace (keep existing selection intact), but still add successfully matched items in Append fashion
   - If all batches succeeded AND mode is "Replace" → `selection.clear()` then add all staged matched items
   - If mode is "Append" → add all staged matched items (regardless of batch failures)
8. Show unmatched panel if any
9. Show toast summary: "Matched X, not found Y, failed Z"

### API Client Addition (`api.ts`)

```typescript
export interface BibtexMatchedItem {
  bibtex_key: string
  paper_id: string
  match_method: 'doi' | 'title'
  title: string
  year: string | null
  venue: string | null
  authors: string[]
}

export interface BibtexUnmatchedItem {
  bibtex_key: string
  title: string | null
  search_query: string
}

export interface BibtexMatchResult {
  matched: BibtexMatchedItem[]
  unmatched: BibtexUnmatchedItem[]
  stats: { total: number; matched: number; unmatched: number }
}

export async function matchBibtex(bibtexRaw: string): Promise<BibtexMatchResult> {
  const url = buildUrl('/papers/match-bibtex')
  const data = await fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bibtex_raw: bibtexRaw }),
  })
  return data as BibtexMatchResult
}
```

## Batch Strategy

- **Batch size:** 50 entries per request
- **Splitting:** Frontend splits raw `.bib` text by `@` entry boundaries (no parsing needed)
- **Progress:** Per-batch progress displayed via existing Progress component
- **Error resilience:** If a batch request fails (network/server error), record it as a failed batch, continue with remaining batches. Failed entries are tracked separately and reported in the toast ("failed Z"). They are neither matched nor unmatched — the user sees them clearly as "not processed."
- **Expected scale:** < 200 entries typical, so 4 batches max

## Files to Modify

### Backend
- `python/deepresearch_flow/paper/snapshot/api.py` — new endpoint `POST /papers/match-bibtex`
- `python/deepresearch_flow/paper/snapshot/db.py` (or `db_ops.py`) — matching logic, reuse existing utilities

### Frontend
- `frontend/src/views/SelectedView.vue` — import button, mode popover, unmatched panel, batch logic
- `frontend/src/lib/api.ts` — `matchBibtex()` function
- `frontend/src/types/api.ts` — `BibtexMatchResult` type (optional, can inline)

### Tests
- `python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py` — API endpoint tests (DOI match, title match, ambiguous title, year mismatch, unmatched, mixed batch, malformed input, parse error entries)
