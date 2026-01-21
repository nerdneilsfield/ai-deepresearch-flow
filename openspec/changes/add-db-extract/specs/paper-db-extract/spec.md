## ADDED Requirements
### Requirement: Extract matched artifacts
The system SHALL provide a `paper db extract` command that builds two datasets and exports only matched artifacts.

#### Scenario: Extract JSON by PDF coverage
- **WHEN** the user runs `paper db extract` with `--input-json` and `--pdf-root`
- **THEN** the output JSON SHALL include only JSON entries matched to PDFs

#### Scenario: Extract JSON by Markdown coverage
- **WHEN** the user runs `paper db extract` with `--input-json` and `--md-root`
- **THEN** the output JSON SHALL include only JSON entries matched to Markdown files

#### Scenario: Extract translated Markdown by PDF coverage
- **WHEN** the user runs `paper db extract` with `--pdf-root`, `--md-translated-root`, and `--lang`
- **THEN** the output translated directory SHALL include only translated Markdown files matched to PDFs

#### Scenario: Extract translated Markdown by Markdown coverage
- **WHEN** the user runs `paper db extract` with `--md-root`, `--md-translated-root`, and `--lang`
- **THEN** the output translated directory SHALL include only translated Markdown files matched to Markdown files

### Requirement: Matching logic parity
The system SHALL use the same matching logic as `paper db compare`/`paper db serve`.

#### Scenario: Matching parity enforced
- **WHEN** extract resolves matches
- **THEN** it SHALL rely on the same matching rules as compare/serve

### Requirement: JSON output format preservation
The system SHALL preserve the original JSON envelope format when exporting matched JSON entries.

#### Scenario: Envelope preserved
- **WHEN** the input JSON is an object with `template_tag` and `papers`
- **THEN** the output JSON SHALL preserve that structure and only filter `papers`

#### Scenario: List preserved
- **WHEN** the input JSON is a raw list of papers
- **THEN** the output JSON SHALL be a list of matched papers

### Requirement: Preserve translated directory structure
The system SHALL preserve relative paths when exporting translated Markdown files.

#### Scenario: Directory structure preserved
- **WHEN** an output translated root is provided
- **THEN** each exported file SHALL be written to the output directory using its path relative to its source input root

#### Scenario: Multiple input roots
- **WHEN** a file is matched from one of multiple input translated roots
- **THEN** its relative path SHALL be calculated from that specific input root

### Requirement: Output safety and feedback
The system SHALL overwrite existing files in the output destination and report execution statistics.

#### Scenario: Overwrite existing
- **WHEN** the output file already exists
- **THEN** the system SHALL overwrite it without error

#### Scenario: Completion summary
- **WHEN** the extraction completes
- **THEN** the system SHALL print the count of extracted JSON entries and/or copied files


### Requirement: CSV report compatibility
The system SHALL export a CSV report compatible with `paper db compare`.

#### Scenario: CSV export
- **WHEN** the user passes `--output-csv`
- **THEN** the system SHALL write a report with match status and match type for all items
