## MODIFIED Requirements
### Requirement: Merge paper databases
The system SHALL provide `paper db merge` subcommands for merging paper JSON inputs.

#### Scenario: Merge library JSON inputs
- **WHEN** the user runs `paper db merge library` with multiple JSON inputs of the same template
- **THEN** the command merges entries across libraries into a single JSON output

#### Scenario: Merge template JSON inputs
- **WHEN** the user runs `paper db merge templates` with multiple JSON inputs from the same library
- **THEN** the command merges per-paper entries across templates into one JSON output

#### Scenario: Shared fields precedence
- **WHEN** multiple template entries define the same shared field
- **THEN** the value from the first input JSON is kept
