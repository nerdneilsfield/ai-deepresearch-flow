## ADDED Requirements
### Requirement: TranslateGemma-style prompt framing
The translator prompt SHALL include the professional translator framing and the blank-line notice.

#### Scenario: Prompt framing
- **WHEN** translation prompts are generated
- **THEN** the user message SHALL include the TranslateGemma-style framing with source/target language names and codes

#### Scenario: Blank-line notice
- **WHEN** translation input is embedded
- **THEN** the prompt SHALL mention there are two blank lines before the text
