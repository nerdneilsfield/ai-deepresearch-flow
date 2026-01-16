## ADDED Requirements
### Requirement: Recognize fix-mermaid command
The system SHALL provide `recognize fix-mermaid` to validate and repair Mermaid diagrams in markdown and JSON outputs.

#### Scenario: Fix markdown Mermaid blocks in place
- **WHEN** `recognize fix-mermaid --in-place` is run on markdown inputs
- **THEN** malformed Mermaid blocks are repaired if possible and the file is updated

#### Scenario: Fix JSON Mermaid blocks to output directory
- **WHEN** `recognize fix-mermaid --output <dir>` is run with `--json` inputs
- **THEN** the updated JSON preserves its original structure and only Mermaid fields are modified

#### Scenario: Auto-detect JSON inputs
- **WHEN** `recognize fix-mermaid` is invoked with JSON file inputs and `--json` is omitted
- **THEN** the command auto-enables JSON mode

### Requirement: Mermaid validation and error reporting
The system SHALL validate Mermaid diagrams via `mmdc` and emit a report containing file paths and line numbers for remaining failures.

#### Scenario: Report unresolved diagrams
- **WHEN** a Mermaid block fails validation after repair attempts
- **THEN** the report includes the file path, line number, Mermaid block, and error messages

### Requirement: Validation-only mode
The system SHALL support a validation-only mode that scans Mermaid diagrams and reports error counts without modifying files or calling the LLM.

#### Scenario: Count errors without fixing
- **WHEN** `recognize fix-mermaid --only-show-error` is invoked
- **THEN** the command reports total diagram count and invalid diagram count without writing outputs

### Requirement: Batched LLM repair with context
The system SHALL batch Mermaid repair requests and include per-diagram context to improve quality while reducing API calls.

#### Scenario: Batch repair with context
- **WHEN** multiple Mermaid blocks in a file are invalid
- **THEN** the system sends them in batches (default size 10) and includes surrounding context for each block in the repair request

### Requirement: Mermaid validation workspace
The system SHALL use a temporary working directory under `/tmp/mermaid` for `mmdc` validation artifacts.

#### Scenario: Temporary validation files
- **WHEN** `recognize fix-mermaid` runs Mermaid validation
- **THEN** the command writes transient Mermaid input/output files under `/tmp/mermaid`
