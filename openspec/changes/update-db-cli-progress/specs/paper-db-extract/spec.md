## ADDED Requirements
### Requirement: Show extract progress
The system SHALL display progress bars for scanning, matching, and copying during `paper db extract`.

#### Scenario: Scan and match progress
- **WHEN** the user runs `paper db extract` with reference roots
- **THEN** the system SHALL show progress for scanning inputs and matching entries

#### Scenario: Copy progress
- **WHEN** the system exports source or translated Markdown
- **THEN** the system SHALL show progress for copied files
