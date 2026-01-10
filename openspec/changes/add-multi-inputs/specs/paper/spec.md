## MODIFIED Requirements

### Requirement: Markdown input discovery
The system SHALL accept markdown inputs from a file path and/or a directory path.
The system SHALL accept multiple input paths in a single run.
When a directory is provided, the system SHALL recursively discover `*.md` files by default.
The system SHALL support restricting discovered files using a glob pattern.
When multiple inputs are provided, the system SHALL de-duplicate discovered files.

#### Scenario: Directory recursion default
- **WHEN** the user runs `deepresearch-flow paper extract --input ./docs`
- **THEN** the tool recursively collects `*.md` files under `./docs`

#### Scenario: Glob filtering
- **WHEN** the user runs `deepresearch-flow paper extract --input ./docs --glob '**/output.md'`
- **THEN** only matching markdown files are processed

#### Scenario: Multiple inputs are combined
- **WHEN** the user runs `deepresearch-flow paper extract --input ./docs --input ./more-docs`
- **THEN** the tool processes the union of matching markdown files from both directories
