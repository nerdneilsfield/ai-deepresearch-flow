## ADDED Requirements
### Requirement: Optional institution extraction
Built-in templates `simple`, `deep_read`, and `eight_questions` SHALL accept an optional `paper_institutions` field without breaking validation.

#### Scenario: Institution present
- **WHEN** the source document includes author affiliations
- **THEN** the output SHOULD include `paper_institutions` as a list of institution names

#### Scenario: Institution missing
- **WHEN** affiliations are not available
- **THEN** the output MAY omit `paper_institutions` or provide an empty list

### Requirement: Diagram guidance without new schema fields
The `deep_read` and `eight_questions` prompts SHALL instruct models to include diagrams inside existing text fields when relevant.

#### Scenario: Deep_read diagrams
- **WHEN** the paper provides content for flowcharts, architecture, data flow, or performance trade-offs
- **THEN** the output SHOULD embed `mermaid` or `markmap` code blocks within the specified modules without adding new JSON fields

#### Scenario: Eight_questions architecture diagram
- **WHEN** the paper describes a system or model architecture
- **THEN** the output SHOULD embed a `mermaid` architecture diagram inside question 3
