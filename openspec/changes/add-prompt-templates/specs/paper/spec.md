## ADDED Requirements

### Requirement: Built-in prompt templates
The system SHALL provide multiple built-in prompt templates for paper reading workflows.
The templates SHALL be selectable by a stable template name.

#### Scenario: Select built-in prompt template
- **WHEN** the user runs `deepresearch-flow paper db render-md --template-name deep_read`
- **THEN** the renderer uses the built-in template named `deep_read`

### Requirement: Output language parameter
The system SHALL allow users to pass an output language string that is available to templates as `output_language`.

#### Scenario: Pass output language to template
- **WHEN** the user runs `deepresearch-flow paper db render-md --template-name deep_read --language zh`
- **THEN** the rendered output includes the language directive populated with `zh`
