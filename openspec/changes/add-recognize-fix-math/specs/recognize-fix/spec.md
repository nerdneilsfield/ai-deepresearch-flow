## ADDED Requirements
### Requirement: Recognize fix-math command
The system SHALL provide `recognize fix-math` to validate and repair `$...$` and `$$...$$` formulas in markdown and JSON outputs.

#### Scenario: Fix markdown formulas in place
- **WHEN** `recognize fix-math --in-place` is run on markdown inputs
- **THEN** malformed formulas are repaired if possible and the file is updated

#### Scenario: Fix JSON formulas to output directory
- **WHEN** `recognize fix-math --output <dir>` is run with `--json` inputs
- **THEN** the updated JSON preserves its original structure and only formula fields are modified

#### Scenario: Auto-detect JSON inputs
- **WHEN** `recognize fix-math` is invoked with JSON file inputs and `--json` is omitted
- **THEN** the command auto-enables JSON mode

### Requirement: Math validation and error reporting
The system SHALL validate formulas via pylatexenc and KaTeX (Node helper when available) and emit a report containing file paths and line numbers for remaining failures.

#### Scenario: Report unresolved formulas
- **WHEN** a formula fails validation after repair attempts
- **THEN** the report includes the file path, line number, delimiter type, and error messages

### Requirement: Validation-only mode
The system SHALL support a validation-only mode that scans formulas and reports error counts without modifying files or calling the LLM.

#### Scenario: Count errors without fixing
- **WHEN** `recognize fix-math --only-show-error` is invoked
- **THEN** the command reports total formula count and invalid formula count without writing outputs

### Requirement: Batched LLM repair with context
The system SHALL batch formula repair requests and include per-formula context to improve quality while reducing API calls.

#### Scenario: Batch repair with context
- **WHEN** multiple formulas in a file are invalid
- **THEN** the system sends them in batches (default size 10) and includes surrounding context for each formula in the repair request
