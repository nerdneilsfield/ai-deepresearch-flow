## ADDED Requirements

### Requirement: Extract summary statistics
The system SHALL display a summary table after `paper extract` runs that includes input, prompt, and output character totals.
The summary SHALL include estimated prompt tokens, completion tokens, and total tokens.
The summary SHALL include total duration, average time per document, and throughput (docs/min and tokens/sec).
The system SHALL estimate tokens using the same ratio as the existing dry-run estimate.
The system SHALL include retries in the prompt/output totals.

#### Scenario: Summary reports character totals
- **WHEN** the user runs `deepresearch-flow paper extract --input ./docs --model openai/gpt-4o-mini`
- **THEN** the command prints a summary table that includes input characters, prompt characters, output characters, estimated prompt/completion tokens, and total duration

### Requirement: Dry-run summary statistics
The system SHALL display input, prompt, and output character totals with estimated tokens during dry-run.
During dry-run, the system SHALL report output character totals and completion tokens as zero.

#### Scenario: Dry-run reports character totals
- **WHEN** the user runs `deepresearch-flow paper extract --input ./docs --dry-run --model openai/gpt-4o-mini`
- **THEN** the command prints input, prompt, and output character totals with estimated prompt/completion tokens

### Requirement: Extract progress token ticker
The system SHALL display a running estimate of prompt, completion, and total tokens alongside the extraction progress indicator.

#### Scenario: Progress ticker shows token totals
- **WHEN** the user runs `deepresearch-flow paper extract --input ./docs --model openai/gpt-4o-mini`
- **THEN** the progress indicator shows running estimated prompt/completion/total token counts
