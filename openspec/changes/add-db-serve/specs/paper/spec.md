## ADDED Requirements

### Requirement: Database web viewer
The system SHALL provide a `deepresearch-flow paper db serve` command to start a local, read-only web UI for a paper database JSON file.
The server SHALL load and index the database at startup for responsiveness.

#### Scenario: Serve command starts server
- **WHEN** the user runs `deepresearch-flow paper db serve --input paper_infos.json`
- **THEN** the command starts a local HTTP server that serves a web UI

### Requirement: Web UI filtering and infinite scroll
The web UI SHALL provide filtering by title query, tags, authors, year/month, and venue.
The paper list UI SHALL support infinite scrolling using paginated API requests.

#### Scenario: Filter list by tag
- **WHEN** the user selects a tag filter in the UI
- **THEN** the list shows only matching papers

### Requirement: Template-based rendering
The detail view SHALL render paper content from JSON using a render template.
The default template selection SHALL use the paper's `prompt_template` when present.
The rendered output SHALL support Mermaid diagrams and KaTeX math.

#### Scenario: Detail view uses per-paper template
- **WHEN** a paper record has `prompt_template: eight_questions`
- **THEN** the detail view uses the matching render template

### Requirement: Statistics dashboard
The web UI SHALL provide a stats page that visualizes the database using charts.

#### Scenario: Stats page renders charts
- **WHEN** the user opens `/stats`
- **THEN** the page displays charts for year/month and top tags/authors/venues
