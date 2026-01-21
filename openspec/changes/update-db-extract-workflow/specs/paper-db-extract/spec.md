## ADDED Requirements
### Requirement: Export matched source Markdown
The system SHALL export matched source Markdown files to a new root directory.

#### Scenario: Export source Markdown by PDF coverage
- **WHEN** the user runs `paper db extract` with `--md-source-root`, `--output-md-root`, and `--pdf-root`
- **THEN** the system SHALL copy matched Markdown files into the output directory

#### Scenario: Preserve relative paths
- **WHEN** a matched Markdown file is exported
- **THEN** the system SHALL preserve its path relative to the input `--md-source-root`
