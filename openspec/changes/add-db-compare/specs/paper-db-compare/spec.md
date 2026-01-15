## ADDED Requirements

### Requirement: Compare two datasets
The system SHALL provide a `paper db compare` command that compares two datasets (A/B) built from JSON files, PDF roots, Markdown roots, or translated Markdown roots.

#### Scenario: Compare JSON to PDF roots
- **WHEN** the user runs `paper db compare` with JSON inputs on side A and PDF roots on side B
- **THEN** the output SHALL report counts for items present in both, only in A, and only in B

#### Scenario: Compare JSON to Markdown roots
- **WHEN** the user compares JSON inputs with Markdown roots
- **THEN** the output SHALL report matched and unmatched items using the same matching logic as db serve

### Requirement: Matching logic parity with db serve
The system SHALL use the same matching logic as `paper db serve` to resolve JSON items against PDF/Markdown/translated Markdown files.

#### Scenario: Match type surfaced
- **WHEN** a JSON item is matched to a file by filename, title, or other db serve heuristics
- **THEN** the compare output SHALL include a `match_type` describing the resolution path

#### Scenario: Data-layer matching parity
- **WHEN** compare builds its matches
- **THEN** it SHALL rely on the same data-layer matching rules used by db serve (db_ops parity)

### Requirement: Language-specific translated comparison
The system SHALL support language-specific comparisons for translated Markdown datasets.

#### Scenario: Compare translated outputs by language
- **WHEN** the user provides `--lang zh` with translated Markdown inputs
- **THEN** the compare results SHALL only consider translated files for the specified language

#### Scenario: Missing language parameter
- **WHEN** the user provides translated Markdown inputs without `--lang`
- **THEN** the system SHALL return a validation error instructing the user to specify `--lang`

### Requirement: Output summaries and CSV export
The system SHALL render rich summary tables and sample lists in the terminal, and export full results to CSV when requested.

#### Scenario: CSV export
- **WHEN** the user passes `--output-csv /path/report.csv`
- **THEN** the system SHALL write a CSV containing every item, its side, match status, and match_type
