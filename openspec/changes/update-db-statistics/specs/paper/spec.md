## ADDED Requirements

### Requirement: Database statistics reporting
The system SHALL provide a `paper db statistics` command that summarizes the database using aggregated counts.
The report SHALL include distributions for publication year, publication month, venue, authors, and tags.
The report SHALL prefer BibTeX metadata per-field when present and fall back to extracted fields when missing.
The report SHALL normalize publication month values to a consistent representation.
The report SHALL display a distribution bar for year and month summaries.
The report SHALL support a `--top-n` option that controls how many rows are shown for ranked tables.
The report SHALL display counts and percentages for each distribution.

#### Scenario: Statistics report uses rich tables
- **WHEN** the user runs `deepresearch-flow paper db statistics --input paper_infos.json`
- **THEN** the command prints a rich-formatted report with tables for year, month, venue, authors, and tags

#### Scenario: BibTeX is preferred when present
- **WHEN** a paper entry contains BibTeX metadata
- **THEN** the statistics report uses BibTeX fields for year/month/venue when available and falls back per-field when missing

#### Scenario: Normalize months
- **WHEN** papers contain month values in mixed formats (e.g., \"Jan 2024\", \"2024-01-01\")
- **THEN** the statistics report groups them under a consistent month label

#### Scenario: Top-N option
- **WHEN** the user runs `deepresearch-flow paper db statistics --input paper_infos.json --top-n 10`
- **THEN** the report limits ranked tables to 10 rows
