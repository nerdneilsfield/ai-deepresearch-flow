## ADDED Requirements
### Requirement: Show transfer progress
The system SHALL display progress during `paper db transfer-pdfs` operations.

#### Scenario: Copy progress
- **WHEN** the user runs `paper db transfer-pdfs` with `--copy`
- **THEN** the system SHALL show progress for each copied file

#### Scenario: Move progress
- **WHEN** the user runs `paper db transfer-pdfs` with `--move`
- **THEN** the system SHALL show progress for each moved file
