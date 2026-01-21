## ADDED Requirements
### Requirement: Transfer PDFs from list
The system SHALL provide a `paper db transfer-pdfs` command that copies or moves PDFs listed in a text file to a destination directory.

#### Scenario: Copy PDF list
- **WHEN** the user runs `paper db transfer-pdfs` with `--input-list`, `--output-dir`, and `--copy`
- **THEN** the system SHALL copy each listed PDF to the output directory

#### Scenario: Move PDF list
- **WHEN** the user runs `paper db transfer-pdfs` with `--input-list`, `--output-dir`, and `--move`
- **THEN** the system SHALL move each listed PDF to the output directory
