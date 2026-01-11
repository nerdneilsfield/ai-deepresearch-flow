## ADDED Requirements

### Requirement: Recognize progress indicators
The system SHALL display a progress indicator for `recognize md embed`, `recognize md unpack`, and `recognize organize` that reflects the number of processed items.

#### Scenario: Show progress for markdown embedding
- **WHEN** the user runs `deepresearch-flow recognize md embed -i ./docs --output ./out`
- **THEN** the command displays a progress bar that advances as each markdown file finishes

### Requirement: Recognize summary output
The system SHALL print a completion summary for recognize commands that includes total items, successes, failures, and output locations.
The summary SHALL report output locations as relative paths when possible.

#### Scenario: Summary includes counts
- **WHEN** a recognize command finishes processing
- **THEN** it prints a summary table with totals and outcome counts

### Requirement: Recognize dry-run mode
The system SHALL support a `--dry-run` flag for recognize commands to report planned outputs without writing files.
When output directories are non-empty and `--dry-run` is not used, the system SHALL warn before processing.

#### Scenario: Dry-run previews outputs
- **WHEN** the user runs `deepresearch-flow recognize organize --dry-run -i ./results --output-simple ./out`
- **THEN** the command prints the planned outputs without writing files

#### Scenario: Warn on non-empty outputs
- **WHEN** the user runs a recognize command targeting a non-empty output directory without `--dry-run`
- **THEN** the command prints a warning before processing

### Requirement: Verbose recognize logging
The system SHALL support a `--verbose` flag for recognize commands that enables debug logging.

#### Scenario: Verbose logging enabled
- **WHEN** the user runs a recognize command with `--verbose`
- **THEN** the command logs debug-level details
