## ADDED Requirements

### Requirement: Homepage availability and template filters
The system SHALL provide multi-select filters on the papers homepage for PDF availability, Source availability, Summary availability, and summary template tags.

#### Scenario: Filter papers with PDFs
- **WHEN** the user selects "With PDF"
- **THEN** the list shows only papers that have PDFs

#### Scenario: Filter papers without Source
- **WHEN** the user selects "Without Source"
- **THEN** the list shows only papers that do not have Source

#### Scenario: Filter papers with Summary and template tag
- **WHEN** the user selects "With Summary" and template tag "simple"
- **THEN** the list shows only papers that have summaries and include the selected template tag

### Requirement: Filter query input
The system SHALL provide a filter query input that can express the same filters as the multi-select controls.

#### Scenario: Query filters by multiple criteria
- **WHEN** the user enters `pdf:yes source:no tmpl:simple` in the filter input
- **THEN** the list shows only papers that match those criteria

### Requirement: Summary template indicators in list
The system SHALL display available summary template tags for each paper in the papers list.

#### Scenario: Show template tags for a paper
- **WHEN** a paper has one or more summary templates
- **THEN** the list item renders the template tags for that paper
