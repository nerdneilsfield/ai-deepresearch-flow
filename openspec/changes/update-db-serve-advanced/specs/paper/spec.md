## MODIFIED Requirements

### Requirement: Database web viewer
The system SHALL provide a `deepresearch-flow paper db serve` command to start a local, read-only web UI for a paper database JSON file.
The server SHALL load and index the database at startup for responsiveness.
The web UI SHALL provide unified search using a single query input.
The search input SHALL support field filters (title/author/tag/venue/year/month), quoted phrases, negation, and term-level OR.
The UI SHALL provide an Advanced Search helper to build queries.
The server SHALL optionally accept a BibTeX input to enrich metadata, and the merge SHALL be field-level with fallback to extracted values.
The server SHALL optionally accept one or more markdown roots to locate original markdown sources for papers.
The server SHALL optionally accept one or more PDF roots to locate PDFs and display them in-page via pdf.js.

#### Scenario: Unified query search
- **WHEN** the user searches using a query like `tag:fpga year:2023..2025 -survey`
- **THEN** the UI filters papers accordingly

#### Scenario: BibTeX enrichment improves stats
- **WHEN** the user provides a BibTeX file
- **THEN** stats and per-paper metadata prefer BibTeX year/month/venue fields when available and fall back otherwise

#### Scenario: Source markdown view
- **WHEN** the user provides one or more markdown roots
- **THEN** the paper detail page can switch between rendered summary and the original markdown source when present

#### Scenario: PDF view
- **WHEN** the user provides one or more PDF roots
- **THEN** the paper detail page can display the matched PDF using pdf.js
