## ADDED Requirements

### Requirement: Snapshot build command
The system SHALL provide a CLI command to build a production snapshot from one or more paper JSON inputs and optional BibTeX metadata.
The command SHALL output a portable SQLite database file (`paper_snapshot.db`) and a static export directory for assets.

#### Scenario: Build snapshot from JSON + BibTeX
- **WHEN** the user runs `paper db snapshot build` with `--input`, `--bibtex`, and relevant roots
- **THEN** the system produces `paper_snapshot.db` and the static export directories

### Requirement: Stable paper identity
The system SHALL assign each paper a stable `paper_id` derived from a deterministic `paper_key`.
The system SHALL prefer DOI-based identity when DOI is available.
The system SHALL NOT require the raw DOI string to be used as a URL path segment.
The system SHALL derive `paper_id` as `sha256("v1|" + paper_key)` truncated to 32 lowercase hex characters.
The snapshot database SHALL store `paper_key` and `paper_key_type` (`doi|arxiv|bib|meta`) for each paper.

#### Scenario: DOI-based identity
- **WHEN** a paper has a BibTeX DOI field
- **THEN** the system uses `doi:<canonical-doi>` as the `paper_key` and derives a stable `paper_id`

#### Scenario: Fallback identity
- **WHEN** a paper does not have a DOI
- **THEN** the system falls back to arXiv, BibTeX key, or metadata-derived identity in that order

### Requirement: Summary export schema
The system SHALL export `summary/<paper_id>.json` for each paper.
The summary export SHALL include at least `paper_id`, `paper_title`, and `summary` (markdown) fields.
When a paper has multiple template outputs, the snapshot build SHALL export a per-template summary file at `summary/<paper_id>/<template_tag>.json` and record the available summary templates in the snapshot DB.

#### Scenario: Static summary hosting
- **WHEN** a client downloads `summary/<paper_id>.json` from the static host
- **THEN** the client can render the summary without calling the API

#### Scenario: Multiple template summaries
- **WHEN** a paper includes multiple template outputs with different `summary` values
- **THEN** the snapshot build exports one summary JSON per template tag under `summary/<paper_id>/`

### Requirement: Canonical DOI normalization
The system SHALL canonicalize DOI values by stripping `doi:` and `https://doi.org/` prefixes and lowercasing the value.
The system SHALL URL-decode percent-encoded sequences in DOI values prior to canonicalization.

#### Scenario: DOI prefix stripping
- **WHEN** the input DOI is `https://doi.org/10.1000/XYZ`
- **THEN** the canonical DOI is `10.1000/xyz`

#### Scenario: DOI URL decoding
- **WHEN** the input DOI is `10.1000%2Fxyz`
- **THEN** the canonical DOI is `10.1000/xyz`

### Requirement: Canonical arXiv normalization
The system SHALL canonicalize arXiv identifiers by stripping `arxiv:` and `https://arxiv.org/abs/` prefixes, lowercasing, and removing version suffixes.

#### Scenario: arXiv version stripping
- **WHEN** the input arXiv id is `https://arxiv.org/abs/2301.00001v3`
- **THEN** the canonical arXiv id is `2301.00001`

### Requirement: Canonical metadata fallback normalization
When using `meta:<hash>` identity, the system SHALL normalize the input fields to reduce churn from punctuation, casing, and author ordering.

#### Scenario: Author order change
- **WHEN** the same authors appear in a different order across two builds
- **THEN** the metadata normalization yields the same `meta:<hash>` value

### Requirement: Identity continuity across rebuilds
The snapshot build command SHALL accept an optional previous snapshot database input.
When a previous snapshot is provided, the system SHALL reuse existing `paper_id` values when any known identity key for a paper matches a key in the previous snapshot.
When a previous snapshot is provided, the system SHALL treat `doi`, `arxiv`, and `bib` identity keys as stronger than `meta` identity keys for reuse decisions.
If multiple candidate identity keys match different existing `paper_id` values, the system SHALL select the match using key strength order (`doi` > `arxiv` > `bib` > `meta`) and record an identity conflict.
The snapshot database SHALL store an alias mapping of all known `paper_key` values to the chosen `paper_id`.

#### Scenario: DOI becomes available after first build
- **WHEN** a paper is first built without a DOI and later rebuilt with a DOI
- **THEN** the system reuses the original `paper_id` and records both keys as aliases

#### Scenario: Conflicting matches resolve by key strength
- **WHEN** a paper matches a `meta` alias in the previous snapshot but also matches a `doi` alias that maps to a different `paper_id`
- **THEN** the builder selects the `paper_id` from the `doi` alias and records an identity conflict

### Requirement: Weak-key collision guard
When reusing a `paper_id` via a `meta:<hash>` key match, the snapshot builder SHALL store a `meta_fingerprint` derived from the normalized `(title, authors, year, venue)` fields.
The stored `meta_fingerprint` representation SHALL NOT be solely a cryptographic hash digest and SHALL preserve enough structure to support similarity/distance checks (for example, storing normalized fields as JSON and computing title similarity and author overlap).
If a `meta:<hash>` match is found but the current `meta_fingerprint` diverges beyond a configured threshold from the prior `meta_fingerprint`, the builder SHALL treat it as an identity conflict and SHALL NOT reuse the prior `paper_id`.

#### Scenario: Meta-key collision does not merge unrelated papers
- **WHEN** two different papers produce the same `meta:<hash>` value across builds due to similar metadata
- **THEN** the builder detects the divergence and assigns a new `paper_id` instead of reusing the prior one

#### Scenario: Minor metadata edits remain within threshold
- **WHEN** a paper’s metadata is corrected in a minor way across builds (for example, small title edits) and still refers to the same paper
- **THEN** the builder considers the fingerprints sufficiently similar and reuses the prior `paper_id`

### Requirement: Snapshot build metadata
The snapshot build SHALL generate a `snapshot_build_id` for each build.
The snapshot database SHALL store the `snapshot_build_id` so the API can use it for cache-busting of build-dependent static objects.

#### Scenario: Snapshot build id is stored
- **WHEN** a snapshot build completes successfully
- **THEN** the snapshot DB contains a `snapshot_build_id` value retrievable by the API

### Requirement: Static artifact layout
The system SHALL export PDFs, Markdown, translations, and images into a static directory using content-hash filenames.
The exported Markdown SHALL reference extracted images using relative `images/<hash>.<ext>` URLs.

#### Scenario: Content-hash URLs
- **WHEN** the source Markdown changes
- **THEN** the exported `/md/<hash>.md` path changes because the content hash changes

#### Scenario: Offline image resolution
- **WHEN** an exported Markdown file is included in a download package where an `images/` directory contains the referenced images
- **THEN** opening the Markdown locally renders images correctly using relative paths

### Requirement: Summary and manifest exports
The system SHALL export per-paper summary and manifest artifacts for static hosting.
Each manifest SHALL include URLs (or content hashes) for `pdf`, `source markdown`, `translated markdown`, `summary`, and the list of referenced images.
Each manifest SHALL include a precomputed `folder_name` and a shorter fallback `folder_name_short` suitable for ZIP packaging.
Each manifest SHALL include per-file relative paths suitable for ZIP packaging.
Each image entry in the manifest SHALL include a `status` field indicating whether the static file is present (`available`) or missing (`missing`).

#### Scenario: Download manifest generation
- **WHEN** building the snapshot for a paper that has PDF, source, translation, and images
- **THEN** the system emits `manifest/<paper_id>.json` describing all required assets

#### Scenario: Missing images are represented in the manifest
- **WHEN** a paper references an image in Markdown that is not present in the exported static assets
- **THEN** the manifest includes that image with `status=missing` so clients can handle the incomplete export

### Requirement: Download path sanitization
The snapshot builder SHALL sanitize download folder and filename strings to be safe on common filesystems.
Sanitization SHALL replace filesystem-invalid characters with `_`, collapse whitespace, and enforce reasonable length limits with a defined fallback strategy.

#### Scenario: Title contains invalid characters
- **WHEN** a paper title contains `/` or `:`
- **THEN** the generated folder name and filenames replace those characters with `_`

#### Scenario: Folder name falls back when too long
- **WHEN** a paper has a very long title that would exceed the maximum allowed folder name length after sanitization
- **THEN** the manifest provides `folder_name_short` as a safe fallback name

### Requirement: Full-corpus search index
The snapshot database SHALL include an FTS5 index over metadata, summary, source content, and translated content.
The index SHALL exclude Markdown tables and HTML tables from the indexed corpus.
The indexed corpus SHALL be derived from plain text extracted from the Markdown sources (not the raw Markdown markup).

#### Scenario: Excluding tables
- **WHEN** a Markdown document contains a table block
- **THEN** table cells are excluded from the indexed plain-text corpus

### Requirement: Small-field fuzzy search index
The snapshot database SHALL include a secondary FTS5 index over selected short metadata fields to support typo-tolerant matching.
The default secondary index scope SHALL include `title` and `venue` and SHALL avoid large multi-value fields to constrain index growth.

#### Scenario: Typo-tolerant metadata match
- **WHEN** a user searches with a small typo in a title term
- **THEN** the system can still return relevant matches using the small-field index

### Requirement: CJK character-level support
The indexed corpus SHALL support character-level tokenization for CJK (Chinese/Japanese/Korean) by inserting spaces between consecutive CJK characters prior to FTS indexing.

#### Scenario: CJK query match
- **WHEN** the indexed corpus contains `深 度 学 习` and the user searches for `深度学习`
- **THEN** the paper is returned as a match

### Requirement: Facet browse indexes
The snapshot database SHALL include normalized facet indexes for author, venue, keywords, institutions, tags, and year to support browsing and filtering.
The snapshot database SHALL store `month` derived from publication metadata and SHALL provide a `month` count index for browsing and filtering.
Facet normalization SHALL include lowercasing and collapsing repeated whitespace.

#### Scenario: Browse by institution
- **WHEN** a client requests papers for a given institution
- **THEN** the database returns all associated papers efficiently using indexed join tables

#### Scenario: Browse by month
- **WHEN** a client requests papers for a given month
- **THEN** the database returns all associated papers efficiently using indexed month fields and counts

### Requirement: Metadata relationship cache
The snapshot build SHALL precompute relationship counts between all available metadata facets and store them in the snapshot database to avoid on-demand aggregation.
The cached relationships SHALL include cross-facet links among authors, institutions, venues, keywords, tags, years, months, summary templates, output_language, provider, model, prompt_template, and translation languages.
The cached relationships SHALL exclude self-links for same-facet relationships (for example, an author is not linked to itself in the coauthor list).

#### Scenario: Author coauthor cache
- **WHEN** a paper contains multiple authors
- **THEN** the snapshot stores coauthor relationship counts for each author, excluding self-links

#### Scenario: Cross-metadata cache
- **WHEN** a paper includes keywords, institutions, and a venue
- **THEN** the snapshot stores relationship counts linking the author(s) to those keywords, institutions, and venue
