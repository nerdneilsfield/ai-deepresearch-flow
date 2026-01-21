## ADDED Requirements
### Requirement: Export only-in-B file list
The system SHALL export a newline-delimited list of only-in-B file paths when requested.

#### Scenario: Export missing file list
- **WHEN** the user runs `paper db compare` with `--output-only-in-b`
- **THEN** the system SHALL write each only-in-B item `source_path` as a line in the output file
