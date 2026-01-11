## MODIFIED Requirements

### Requirement: Extract output format
The system SHALL write extract output JSON as an object with `template_tag` and `papers` fields, and SHALL not emit the legacy array-only format.

#### Scenario: Extract writes tagged output
- **WHEN** the user runs `paper extract` with JSON output enabled
- **THEN** the output JSON is an object containing `template_tag` and `papers`
