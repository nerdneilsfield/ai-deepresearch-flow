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
