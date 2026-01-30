## ADDED Requirements

### Requirement: Snapshot API service
The system SHALL provide an API service that reads `paper_snapshot.db` and exposes endpoints for searching and browsing papers.
The API SHALL be suitable for a separately deployed static frontend (no server-side HTML rendering required).
The API SHALL identify papers using `paper_id`.
The API SHALL expose a `stats` endpoint to return aggregate counts suitable for a homepage/dashboard.

#### Scenario: Serve API from snapshot DB
- **WHEN** the user runs `paper db api serve` with a snapshot DB path
- **THEN** the service starts and serves JSON responses for search and browse routes

#### Scenario: Paper detail by ID
- **WHEN** a client requests a paper detail route with a `paper_id`
- **THEN** the API returns metadata and asset URLs for that paper

#### Scenario: Stats endpoint
- **WHEN** a client requests the stats route
- **THEN** the API returns aggregate counts (total, years, months, and top facets)

### Requirement: Pagination for list endpoints
The API SHALL support pagination for list endpoints that return multiple papers.
The API SHALL reject deep pagination requests where `page * page_size` exceeds a configured maximum offset limit.

#### Scenario: Paged search results
- **WHEN** a client requests the second page of a search result set
- **THEN** the API returns only that page of items and includes total/has_more metadata

#### Scenario: Deep pagination attempt
- **WHEN** a client requests `page` and `page_size` such that `page * page_size` exceeds the configured maximum offset
- **THEN** the API returns a validation error instead of executing an expensive query

### Requirement: Search query semantics
The API SHALL support:
- CJK character-level matching (via query rewrite to spaced phrases)
- English word token matching
- Boolean operators `AND` and `OR`
The API SHALL support mixed CJK/Latin queries by rewriting consecutive CJK sequences into spaced phrase segments while preserving Latin/digit segments.
The API SHALL rank results with higher weight on `title` and `summary` than on `source` and `translated` content.

#### Scenario: Boolean query
- **WHEN** a user searches `lidar AND localization`
- **THEN** results include only papers matching both terms

#### Scenario: CJK phrase rewrite
- **WHEN** a user searches `深度学习`
- **THEN** the API rewrites the query to a character-spaced phrase query and returns matching papers

#### Scenario: Mixed CJK-English query rewrite
- **WHEN** a user searches `深度学习 transformer`
- **THEN** the API rewrites the CJK segment into a spaced phrase while preserving the English token and returns matching papers

### Requirement: Snippet output as Markdown
The API SHALL return a `snippet_markdown` field for search results when a query is provided.
The snippet SHALL be plain markdown text and SHALL NOT include HTML.
The snippet SHALL include match markers using the literal strings `[[[` and `]]]`.
The snippet SHALL NOT include the CJK spacing inserted for indexing.

#### Scenario: Markdown snippet
- **WHEN** a client performs a search query
- **THEN** each result includes a `snippet_markdown` excerpt suitable for frontend markdown rendering

#### Scenario: Marker-based highlighting
- **WHEN** a search query matches content in a result
- **THEN** the matching text in `snippet_markdown` is wrapped in `[[[` and `]]]` markers for reliable frontend highlighting

### Requirement: Query limits and validation
The API SHALL enforce limits on user-provided query inputs to reduce the risk of expensive or malformed requests.
The API SHALL limit `q` length and SHALL cap `page_size` to a safe maximum.
The default limits SHALL be:
- `q` max length: 500 characters
- `page_size` max: 100
- max pagination offset: 10,000 items (`page * page_size`)

#### Scenario: Excessive page size
- **WHEN** a client requests a `page_size` larger than the configured maximum
- **THEN** the API returns a validation error or clamps to the maximum

#### Scenario: Excessive query length
- **WHEN** a client requests a search with `q` longer than the configured maximum length
- **THEN** the API returns a validation error

### Requirement: Versioned API routes
The API SHALL expose its HTTP endpoints under a versioned path prefix (for example, `/api/v1/`) to enable future backwards-incompatible changes.

#### Scenario: Versioned path prefix
- **WHEN** a client calls the search endpoint
- **THEN** the request uses a versioned path prefix such as `/api/v1/search`

### Requirement: Asset URL resolution
The API SHALL return canonical URLs for static assets using a configured `static_base_url`, including:
`pdf_url`, `source_md_url`, `translated_md_urls`, `images_base_url`, `summary_url`, and `manifest_url`.
The API SHALL include a cache-busting query parameter based on `snapshot_build_id` for build-dependent objects such as `summary_url` and `manifest_url`.
When a paper has multiple summary templates, the API SHALL expose `summary_urls` mapping template tags to their per-template summary URLs.

#### Scenario: Static base URL
- **WHEN** `static_base_url` is configured as `https://static.example.com`
- **THEN** the API returns asset URLs rooted at `https://static.example.com`

### Requirement: Facet browse endpoints
The API SHALL support browsing by author, venue, keywords, institutions, tags, year, and month, including returning counts and listing papers under a selected facet value.

#### Scenario: Author listing with counts
- **WHEN** a client requests the author facet list
- **THEN** the API returns authors with paper counts sorted by count descending

### Requirement: Facet by-value endpoints
The API SHALL expose by-value facet routes under `/facets/{facet}/by-value/{value}` for both papers and stats.
The `{value}` segment SHALL accept URL-encoded facet values and be normalized in the same way as snapshot facet values.

#### Scenario: Browse by author value
- **WHEN** a client requests `/facets/authors/by-value/alice%20smith/papers`
- **THEN** the API returns papers authored by “alice smith”

### Requirement: Facet-scoped stats endpoints
The API SHALL expose facet-scoped statistics under the `/facets/` namespace for authors, institutions, and venues.
The facet stats response SHALL include totals and per-year/per-month counts scoped to the selected facet value.
The facet stats response SHALL include relationship counts for all cached metadata facets (authors, institutions, venues, keywords, tags, years, months, summary templates, output_language, provider, model, prompt_template, translation languages).

#### Scenario: Author stats by facet
- **WHEN** a client requests `/facets/authors/{author_id}/stats`
- **THEN** the API returns totals and year/month breakdowns limited to that author

#### Scenario: Author knowledge graph stats
- **WHEN** a client requests `/facets/authors/by-value/alice%20smith/stats`
- **THEN** the API returns coauthor, venue, institution, keyword, tag, year, and month relationship counts for that author

### Requirement: CORS-friendly static frontend integration
The API SHALL support CORS for a configured set of allowed origins to enable a separately hosted static frontend.
The API SHALL require no authentication by default.
The deployment documentation SHALL state that the static asset host at `static_base_url` MUST also enable CORS for those origins to support browser ZIP export.

#### Scenario: Browser access from static site
- **WHEN** the frontend origin is configured as allowed
- **THEN** browser requests succeed with appropriate CORS headers
