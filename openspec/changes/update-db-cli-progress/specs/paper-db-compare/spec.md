## ADDED Requirements
### Requirement: Show compare progress
The system SHALL display progress bars for scanning inputs and matching items during `paper db compare`.

#### Scenario: Scan progress
- **WHEN** the user runs `paper db compare` with PDF or Markdown roots
- **THEN** the system SHALL show scan progress for each root type

#### Scenario: Match progress
- **WHEN** the system matches dataset items
- **THEN** the system SHALL show match progress for items being compared
