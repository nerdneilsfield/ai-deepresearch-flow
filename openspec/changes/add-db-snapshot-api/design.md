## Context
The current `paper db serve` workflow is optimized for local use: it builds an in-memory index from JSON and scans Markdown/PDF roots at runtime, then renders a server-owned UI. For production, we want:
- a deterministic, portable build artifact (SQLite snapshot + static assets),
- a stable paper identity (`paper_id`) to attach user/business data across refreshes,
- a fully decoupled frontend that can be hosted as static files,
- an API service that can later be rewritten in another language without changing artifact formats.

## Goals / Non-Goals
- Goals:
  - Produce a `paper_snapshot.db` that is sufficient for API search/browse without access to the original roots.
  - Provide FTS5 search across metadata + summary + source + translated content, with CJK character-level support.
  - Support client-side bulk download (ZIP) of `{pdf, source, translated, summary, images}` with local Markdown image resolution.
  - Provide facet browsing (authors, venue, keywords, institutions, tags, year) with precomputed counts.
- Non-Goals:
  - Authentication and multi-user permissions.
  - Institution/author synonym merging (aliases).
  - Server-side ZIP generation (client builds ZIP).

## Decisions
### Paper identity (`paper_id`)
- Define a stable `paper_key` with precedence:
  1) `doi:<canonical-doi>`
  2) `arxiv:<id>` (if available)
  3) `bib:<bibtex_key>` (if available)
  4) `meta:<hash>` derived from normalized `(title, authors, year, venue)`
- Define `paper_id` as a hashed, URL-safe identifier:
  - `paper_id = sha256("v1|" + paper_key).hexdigest()[:32]` (32 hex chars).
- Canonical DOI normalization:
  - Strip leading `https://doi.org/`, `http://doi.org/`, `doi:`
  - URL-decode percent-encoded sequences (e.g. `%2F`)
  - Lowercase
  - Trim whitespace and trailing punctuation (`.`, `,`, `;`, `)`)

Canonical arXiv normalization:
- Strip leading `https://arxiv.org/abs/`, `http://arxiv.org/abs/`, `arxiv:`
- Lowercase
- Drop version suffix (e.g. `2301.00001v3` → `2301.00001`, `hep-th/9901001v2` → `hep-th/9901001`)

Metadata fallback (`meta:<hash>`) normalization:
- Title: Unicode NFKC → lowercase → remove punctuation/symbols → collapse whitespace
- Authors: normalize each name (NFKC + lowercase + collapse whitespace) and sort to reduce churn from order changes
- Year: extract a 4-digit year when possible; otherwise use `unknown`
- Venue: NFKC + lowercase + collapse whitespace

Debugging support:
- Store `paper_key_type` (`doi|arxiv|bib|meta`) and `paper_key` in the snapshot DB to diagnose identity behavior.

### Identity continuity across weekly rebuilds
To prevent user/business data from losing its association when preferred metadata becomes available (e.g. DOI appears later), snapshot build supports optional continuity:
- Accept an optional previous snapshot DB as input.
- For each paper, compute all candidate keys that are available (`doi`, `arxiv`, `bib`, `meta`).
- If any candidate key matches a key already present in the previous snapshot, reuse the existing `paper_id`.
- Store all known keys in an alias table so future builds can reuse identities even if the preferred key changes.

Key strength and conflict handling:
- Treat `doi`, `arxiv`, and `bib` keys as strong keys; treat `meta` as a weak key.
- Matching SHOULD prefer the strongest available key type.
- If multiple candidate keys match different historical `paper_id` values, the builder records a conflict and selects the match by key strength (doi > arxiv > bib > meta).

Collision guard for weak keys:
- When a `meta` key match is used, the builder stores a `meta_fingerprint` (normalized title/authors/year/venue) for debugging and conflict detection.
- `meta_fingerprint` is NOT a cryptographic hash; it MUST preserve enough structure to support similarity checks (e.g., store normalized fields as JSON, or store a SimHash + supporting normalized strings).
- If a `meta` key match is found but the metadata diverges beyond a configured threshold (e.g., low title similarity or low author overlap), the builder treats it as an identity conflict instead of reusing the `paper_id`.

### Static artifacts
The snapshot build produces:
- `paper_snapshot.db` (SQLite)
- `paper-static/` (static asset root; can be served via CDN)
  - `/pdf/<content-hash>.pdf`
  - `/md/<content-hash>.md`
  - `/md_translate/<lang>/<content-hash>.md`
  - `/images/<image-hash>.<ext>`
- `summary/<paper_id>.json`
- `manifest/<paper_id>.json`

The API returns absolute URLs to these static objects using a configured `static_base_url`.
Configuration sources (from highest to lowest precedence):
1) CLI flags
2) Environment variables
3) `config.toml`

Static host requirements:
- The static host MUST enable CORS for the frontend origin(s) so the browser can `fetch()` assets for ZIP export.

CDN cache strategy:
- Content-hashed assets (`/pdf`, `/md`, `/md_translate`, `/images`) are immutable and can be cached for a long time.
- `summary` and `manifest` objects are paper-id-addressed and can change across builds; the API SHOULD return these URLs with a cache-busting build identifier query parameter (e.g. `?v=<snapshot_build_id>`), and the static host SHOULD honor query strings in cache keys.

### Search and snippets
- Use SQLite FTS5 for search.
- Provide two FTS tables:
  - `paper_fts` (full corpus): `tokenize=unicode61`
  - `paper_fts_trigram` (small fields only): `tokenize=trigram` for typo-tolerance on selected short fields (default: `title` and `venue`)
- The full corpus is built from extracted plain text (not raw Markdown):
  - Include: title/authors/venue/keywords/institutions/year + summary + source + translated
  - Exclude: Markdown tables and HTML tables
  - Strip: Markdown/HTML tags and normalize whitespace
- CJK handling:
  - During indexing, insert spaces between consecutive CJK characters so `unicode61` tokenizes per-character.
  - During query parsing, convert consecutive CJK sequences into a phrase query using the same spacing.
- Mixed-language query rewrite:
  - Tokenize the query by whitespace and boolean operators.
  - For each token segment:
    - If the segment is CJK-only, rewrite it into a quoted phrase of spaced characters.
    - If the segment mixes CJK and Latin/digits, split at CJK↔Latin boundaries and apply the same rule per segment.
  - Treat common CJK punctuation (e.g. `，。、《》、`) as whitespace separators.
- Snippets:
  - API returns `snippet_markdown` as plain markdown text (no HTML).
  - `snippet_markdown` includes match markers using the literal strings `[[[` and `]]]` (no HTML).
  - For display, the API removes the CJK spacing inserted for indexing (spaces between consecutive CJK characters).
  - The frontend MAY transform markers into highlights (e.g. `<mark>`) prior to rendering.

### Facet browsing
- Snapshot DB stores normalized facet values and join tables:
  - `author` + `paper_author`
  - `keyword` + `paper_keyword`
  - `institution` + `paper_institution`
  - `tag` + `paper_tag`
  - `venue` (either normalized in `paper` or a separate table)
  - `year` (indexed on `paper.year`)
- Normalization: lowercase + Unicode normalization + collapse whitespace; no synonym merging.
- Precompute counts for fast listing endpoints.

### Bulk download ZIP (client-side)
- A per-paper manifest enables client-side ZIP generation without parsing Markdown at runtime.
- ZIP layout per paper:
  - Folder name: `{first_author}_{year}_{title}__{paper_id}` (sanitized for filesystem, with truncation and fallback)
  - `source.md` (rewritten to use `images/<hash>.<ext>` relative paths)
  - `translated/<lang>.md`
  - `summary.md` (rendered summary; source is `summary/<paper_id>.json`)
  - `images/<hash>.<ext>` (only images referenced by the paper markdown)
  - `{first_author}_{year}_{title}.pdf` (sanitized)

Sanitization rules (download paths):
- Replace filesystem-invalid characters with `_` (including `/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`)
- Collapse whitespace and trim leading/trailing separators
- Enforce max lengths on `first_author` and `title` segments; include `__{paper_id}` to guarantee uniqueness
- If the computed folder name would exceed the configured max length, fall back to `{first_author}_{year}__{paper_id}` and finally `{paper_id}`

## Risks / Trade-offs
- Full-corpus indexing increases snapshot DB size; avoid `trigram` on large text fields.
- Character-level CJK tokenization improves recall but can increase false positives; phrase-rewrite mitigates this.
- Content extraction (Markdown→text) must be stable to avoid noisy search results.

## Migration Plan
- Add new commands and artifacts without changing `paper db serve`.
- Update docs with a recommended production path.
- Later: optionally switch the legacy UI to consume the new API, or keep it as a local tool.

## Open Questions
- Whether to also export `summary/<paper_id>.md` for direct offline reading (in addition to JSON).
- Whether manifests should be served statically only or also via the API (both are compatible).
