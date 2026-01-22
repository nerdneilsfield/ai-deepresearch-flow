## MODIFIED Requirements
### Requirement: Merge paper databases
The system SHALL provide `paper db merge` subcommands for merging paper JSON inputs.

#### Scenario: Merge library JSON inputs
- **WHEN** the user runs `paper db merge library` with multiple JSON inputs of the same template
- **THEN** the command merges entries across libraries into a single JSON output

#### Scenario: Merge template JSON inputs
- **WHEN** the user runs `paper db merge templates` with multiple JSON inputs from the same library
- **THEN** the command merges per-paper entries across templates into one JSON output, skipping non-matching papers

#### Scenario: Shared fields precedence
- **WHEN** multiple template entries define the same shared field
- **THEN** the value from the first input JSON is kept

#### Scenario: Unmatched papers are skipped
- **WHEN** a paper in a later input does not match any paper in the first input
- **THEN** the paper is excluded from the merged output and reported

#### Scenario: Report mismatched identifiers
- **WHEN** matched papers have different title, date, or source hash values across templates
- **THEN** the merge output reports the differing values for audit
