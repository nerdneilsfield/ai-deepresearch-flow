## ADDED Requirements

### Requirement: Required publication metadata
Built-in schemas SHALL require `publication_date` and `publication_venue` fields.

#### Scenario: Require publication fields in built-in schemas
- **WHEN** a model response omits `publication_date` or `publication_venue`
- **THEN** extraction retries or fails validation per the configured retry policy

### Requirement: Summary template for simple extraction
The `simple` template SHALL require `abstract`, `keywords`, and `summary`.
The `summary` output SHALL be a single paragraph that covers the eight-question aspects.

#### Scenario: Simple template requires summary fields
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-template simple`
- **THEN** the schema requires `abstract`, `keywords`, and `summary` fields

### Requirement: Eight-question workflow
The `eight_questions` workflow SHALL expose eight questions with JSON fields `question1` through `question8`.
The questions SHALL include dataset/implementation settings and metrics/results, and remove the reading-guidance question.
The `deep_read` workflow SHALL include module_c1 through module_c8 aligned to the same eight questions.

#### Scenario: Eight_questions uses eight fields
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-template eight_questions`
- **THEN** the output schema contains required fields `question1` through `question8`

#### Scenario: Deep_read includes module_c8
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-template deep_read`
- **THEN** the output schema contains required field `module_c8`

### Requirement: Two-stage grouping for eight_questions
The `eight_questions` workflow SHALL group questions 1-4 and 5-8 into two extraction stages.

#### Scenario: Two-stage extraction groups
- **WHEN** multi-stage extraction runs for `eight_questions`
- **THEN** stage outputs are produced for `questions_1to4` and `questions_5to8`

### Requirement: Localized render headings
Render templates SHALL localize section headings based on `output_language` for zh/en.
When `output_language` is `zh`, headings SHALL include both Chinese and English labels.

#### Scenario: Render headings in zh
- **WHEN** `output_language` is `zh`
- **THEN** rendered headings include Chinese and English labels

#### Scenario: Render headings in en
- **WHEN** `output_language` is `en`
- **THEN** rendered headings use English labels
