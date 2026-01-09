## ADDED Requirements

### Requirement: Built-in extract and render templates
The system SHALL provide multiple built-in extract prompt templates for paper reading workflows.
The system SHALL provide built-in render templates that correspond to the extract templates.
The templates SHALL be selectable by a stable template name for both extract and render workflows.

#### Scenario: Select built-in extract prompt template
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-template deep_read`
- **THEN** the extractor uses the built-in prompt template named `deep_read`

#### Scenario: Select built-in render template
- **WHEN** the user runs `deepresearch-flow paper db render-md --template-name deep_read`
- **THEN** the renderer uses the built-in template named `deep_read`

### Requirement: Output language parameter
The system SHALL allow users to pass an output language string that is available to extract prompt templates and render templates as `output_language`.

#### Scenario: Pass output language to template
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-template deep_read --language zh`
- **THEN** the extract prompt template includes the language directive populated with `zh`

#### Scenario: Pass output language to render template
- **WHEN** the user runs `deepresearch-flow paper db render-md --template-name deep_read --language zh`
- **THEN** the rendered output includes the language directive populated with `zh`
