## ADDED Requirements
### Requirement: Target JSON for db extract
The system SHALL accept a target JSON database via `--json` for `paper db extract`.

#### Scenario: Extract from target JSON
- **WHEN** the user runs `paper db extract --json db.json`
- **THEN** extraction operates on entries in db.json

### Requirement: Reference inputs for db extract
The system SHALL allow exactly one reference input via `--input-json` or `--input-bibtex`, and SHALL default `--json` to `--input-json` when `--json` is omitted.

#### Scenario: Reference JSON defaults target
- **WHEN** the user runs `paper db extract --input-json refs.json` without `--json`
- **THEN** the command uses refs.json as both target and reference

#### Scenario: Mutual exclusivity
- **WHEN** the user provides both `--input-json` and `--input-bibtex`
- **THEN** the command fails with a validation error

### Requirement: Unmatched reference reporting
The system SHALL include unmatched reference entries in CSV output when `--output-csv` is provided.

#### Scenario: CSV includes unmatched
- **WHEN** `--output-csv` is provided and some references are not matched
- **THEN** the CSV includes rows marked as unmatched

### Requirement: Deduplicate extracted JSON entries
The system SHALL deduplicate extracted JSON entries by normalized title before writing the output JSON.

#### Scenario: Remove duplicate titles
- **WHEN** multiple target entries normalize to the same title
- **THEN** only one entry per normalized title is written to the output JSON

### Requirement: Prefer the most detailed match per reference
The system SHALL keep the most detailed target entry when multiple entries match the same reference input.

#### Scenario: Multiple candidates match one reference
- **WHEN** multiple target entries resolve to the same reference
- **THEN** the output JSON keeps the most detailed entry among them

### Requirement: Normalize BibTeX title formatting
The system SHALL normalize common BibTeX/LaTeX title formatting to improve matching.

#### Scenario: LaTeX-wrapped title matches plain title
- **WHEN** a BibTeX title contains LaTeX commands or math markers
- **THEN** title normalization removes those markers for matching

### Requirement: Author/year fallback matching
The system SHALL fall back to author/year matching when title matching fails.

#### Scenario: Title mismatch but author/year matches
- **WHEN** title-based matching fails but author and year align
- **THEN** the reference is matched using author/year fallback

### Requirement: CSV includes matched and only-in-A entries
The system SHALL include matched and only-in-A entries in CSV output when reference inputs are used.

#### Scenario: CSV includes matched pairs
- **WHEN** `--output-csv` is provided and references are matched
- **THEN** the CSV includes matched pair rows

#### Scenario: CSV includes only-in-A
- **WHEN** `--output-csv` is provided and some target entries have no reference match
- **THEN** the CSV includes rows marked as only_in_A

### Requirement: Strip leading decimal section numbers
The system SHALL strip leading decimal section numbers (e.g., 23.4) during title matching.

#### Scenario: Decimal prefix is ignored
- **WHEN** a title begins with a decimal section number like 23.4
- **THEN** matching ignores the decimal prefix

### Requirement: Preserve numeric token boundaries
The system SHALL avoid merging single-character numeric tokens during title normalization.

#### Scenario: Numeric tokens remain separate
- **WHEN** a title contains tokens like \"23 4 Nebula\" after normalization
- **THEN** numeric tokens remain separate and do not merge into adjacent words
